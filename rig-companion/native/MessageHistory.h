#pragma once
#include "AprsConversations.h"
#include "CommsPacket.h"
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <deque>
#include <map>
#include <string>
#include <vector>

namespace history {
constexpr size_t recentLimit=2000;
constexpr size_t frameLimit=4096;
constexpr uint32_t segmentLimit=1024*1024;
constexpr const char* directory="/htcommander/history";

struct Record {
  uint64_t key=0,utc=0;
  uint32_t revision=1;
  bool initial=true,directed=false,bss=false,mapOnly=false;
  uint8_t auth=0;
  std::string from,text,messageId,peer,owner,target,channel;
  bool sentBss=false;
  uint32_t micros=0;
  comms::Metadata packet;
  bool isComms()const{return !mapOnly && (bss||packet.type!=comms::Type::None);}
};

inline void number(std::string& s,uint64_t value,unsigned bytes){
  for(unsigned i=0;i<bytes;++i){s+=char(value&255);value>>=8;}
}
inline uint64_t numberAt(const std::string& s,size_t pos,unsigned bytes){
  uint64_t value=0;
  for(unsigned i=0;i<bytes;++i)value|=uint64_t(static_cast<unsigned char>(s[pos+i]))<<(8*i);
  return value;
}
inline uint32_t checksum(const std::string& s){
  uint32_t hash=2166136261u;
  for(unsigned char c:s){hash^=c;hash*=16777619u;}
  return hash;
}
inline std::string encode(const Record& record){
  if(!record.key||!record.revision||record.auth>3||record.micros>999999||(!record.utc&&record.micros))return {};
  std::string body;
  number(body,record.key,8);number(body,record.utc,8);number(body,record.revision,4);
  unsigned flags=(record.initial?1:0)|(record.directed?2:0)|(record.isComms()?4:0)|(record.auth<<3)|(record.channel.empty()?0:32);
  if(record.isComms())flags|=record.sentBss?64:128;
  body+=char(flags);
  // Received Comms records have no wire message ID. Use that spare HTM2
  // field for a typed extension so older Classic images can replay the card.
  std::string tag;
  if(record.mapOnly){
    if(record.bss||record.directed||record.sentBss||record.auth||record.packet.type!=comms::Type::None||!record.messageId.empty()||!record.peer.empty())return {};
    tag="@HTC2/M";
  }
  if(record.packet.type!=comms::Type::None){
    if(!record.packet.valid()||record.bss||record.sentBss||record.directed||!record.messageId.empty())return {};
    tag=record.packet.type==comms::Type::Aprs?"@HTC1/P,":"@HTC1/X,";
    char coords[64];std::snprintf(coords,sizeof(coords),"%.17g,%.17g",record.packet.point.lat,record.packet.point.lon);tag+=coords;
  }
  const auto& id=tag.empty()?record.messageId:tag;
  for(const auto* field:{&record.from,&record.text,&id,&record.peer,&record.owner,&record.target,&record.channel}){
    if(field==&record.channel && field->empty())break;
    if(field->size()>frameLimit || body.size()+field->size()+17>frameLimit)return {};
    number(body,field->size(),2);body+=*field;
  }
  number(body,record.micros,3);
  std::string frame="HTM2";number(frame,body.size(),4);number(frame,checksum(body),4);return frame+body;
}
inline bool decode(const std::string& body,Record& r,bool version2=true){
  if(body.size()<33)return false;
  r.key=numberAt(body,0,8);r.utc=numberAt(body,8,8);r.revision=uint32_t(numberAt(body,16,4));
  unsigned flags=static_cast<unsigned char>(body[20]);
  if(!r.key||!r.revision||(flags&0xc0)==0xc0||((flags&0xc0)&&!(flags&4)))return false;
  if(version2 && (flags&4) && !(flags&0xc0))return false;
  r.initial=flags&1;r.directed=flags&2;r.bss=flags&4;r.auth=(flags>>3)&3;
  size_t pos=21;
  for(auto* field:{&r.from,&r.text,&r.messageId,&r.peer,&r.owner,&r.target,&r.channel}){
    if(field==&r.channel && !(flags&32)){field->clear();break;}
    if(body.size()-pos<2)return false;
    auto n=size_t(numberAt(body,pos,2));pos+=2;
    if(n>body.size()-pos)return false;
    field->assign(body,pos,n);pos+=n;
  }
  // Legacy broadcasts recorded the local callsign only on the transmit path.
  r.sentBss=(flags&64)||(r.bss && !(flags&128) && !r.owner.empty() && r.from.rfind("You (",0)==0);
  r.micros=0;
  if(version2){
    if(body.size()-pos!=3)return false;
    r.micros=uint32_t(numberAt(body,pos,3));pos+=3;
    if(r.micros>999999||(!r.utc && r.micros))return false;
  }else if(flags&0xe0)return false;
  r.mapOnly=false;
  if(version2 && r.messageId=="@HTC2/M"){
    if(r.bss||r.directed||r.sentBss||r.auth||!r.peer.empty())return false;
    r.mapOnly=true;r.messageId.clear();
  }
  r.packet={};
  if(version2 && r.bss && !r.sentBss && !r.directed && r.messageId.rfind("@HTC1/",0)==0){
    const auto& id=r.messageId;
    if(id.size()<11 || (id[6]!='P'&&id[6]!='X') || id[7]!=',')return false;
    char* end=nullptr;const char* lat=id.c_str()+8;double a=std::strtod(lat,&end);
    if(end==lat || *end!=',')return false;
    const char* lon=end+1;double b=std::strtod(lon,&end);
    if(end==lon||end!=id.data()+id.size()||!std::isfinite(a)||!std::isfinite(b)||std::abs(a)>90||std::abs(b)>180)return false;
    r.packet={id[6]=='P'?comms::Type::Aprs:comms::Type::Ax25,{a,b}};r.bss=false;r.messageId.clear();
  }
  return pos==body.size();
}

// Replay preserves insertion order when a delivery state changes or a write
// gets retried after the card accepted it but the writer lost confirmation.
class Window {
  std::deque<Record> rows;
  std::map<uint64_t,Record*> byKey;
  size_t limit;
public:
  explicit Window(size_t maximum=recentLimit):limit(maximum){}
  void apply(Record record){
    if(record.mapOnly||!limit)return;
    auto existing=byKey.find(record.key);
    if(existing!=byKey.end()){
      if(record.revision>existing->second->revision)*existing->second=std::move(record);
      return;
    }
    if(!record.initial)return;
    if(rows.size()==limit){byKey.erase(rows.front().key);rows.pop_front();}
    rows.push_back(std::move(record));byKey.emplace(rows.back().key,&rows.back());
  }
  std::deque<Record> release(){byKey.clear();return std::move(rows);}
};

inline std::string path(uint32_t segment,const char* ext="log",const char* folder=directory){
  char suffix[20];std::snprintf(suffix,sizeof(suffix),"/%08lx.%s",static_cast<unsigned long>(segment),ext);
  return std::string(folder)+suffix;
}
inline bool segmentNumber(const std::string& name,uint32_t& id,const char* ext=".log"){
  if(name.size()!=12)return false;
  auto lower=[](char c){return char(c>='A' && c<='Z'?c+('a'-'A'):c);};
  for(size_t i=0;i<4;++i)if(lower(name[8+i])!=ext[i])return false;
  uint32_t value=0;
  for(size_t i=0;i<8;++i){
    char c=lower(name[i]);unsigned digit;
    if(c>='0'&&c<='9')digit=c-'0';else if(c>='a'&&c<='f')digit=c-'a'+10;else return false;
    value=(value<<4)|digit;
  }
  id=value;return id!=0;
}
template<class File> bool readExact(File& file,std::string& data,size_t length){
  data.resize(length);
  size_t offset=0;
  while(offset<length){
    auto n=file.read(reinterpret_cast<uint8_t*>(&data[offset]),length-offset);
    if(n<=0||size_t(n)>length-offset)return false;
    offset+=size_t(n);
  }
  return true;
}

enum class ClearKind:uint8_t {Aprs,Comms,Peer};
struct ClearScope {
  ClearKind kind=ClearKind::Aprs;
  std::string peer;
  bool valid() const{
    if(kind==ClearKind::Peer)return !peer.empty() && peer.size()<=frameLimit-21;
    return peer.empty() && (kind==ClearKind::Aprs || kind==ClearKind::Comms);
  }
  bool matches(bool bss,const std::string& thread) const{
    if(kind==ClearKind::Comms)return bss;
    return !bss && (kind==ClearKind::Aprs || thread==peer);
  }
};

// Immutable clear markers occupy their own segment numbers. Applying the
// cutoffs before filling the RAM window also brings older surviving rows back.
class Journal {
  const char* folder;
  std::string path(uint32_t id,const char* ext="log") const{return history::path(id,ext,folder);}
  uint32_t segment=0;
  size_t offset=0;
  bool ready=false;
  uint32_t tipId=0;
  int64_t tipSize=0;
  std::string tipPath,tipTail;
  void rememberTip(uint32_t id,std::string name,int64_t length,std::string tail){
    tipId=id;tipPath=std::move(name);tipSize=length;tipTail=std::move(tail);
  }
public:
  explicit Journal(const char* location=directory):folder(location){}
  std::string error;
  unsigned tornTails=0;
  bool foundClear=false;
  template<class FS,class Visitor> bool recover(FS& fs,Visitor visit,uint64_t pendingClear=0){
    ready=false;error.clear();tornTails=0;foundClear=false;
    std::vector<uint32_t> segments,markers;
    if(!fs.historySegments(segments,markers)){error="Cannot list message history.";return false;}
    std::sort(segments.begin(),segments.end());
    uint32_t aprsCut=0,commsCut=0,newestPeerCut=0,last=segments.empty()?0:segments.back();
    std::map<std::string,uint32_t> peerCuts;
    for(auto id:markers){
      const auto filename=path(id,"clr");
      const auto length=fs.size(filename);
      auto data=length>=21 && length<=int64_t(frameLimit)?fs.range(filename,0,size_t(length)):std::string{};
      if(data.size()!=size_t(length)||data.size()<21||data.compare(0,4,"HTC1") ||
         numberAt(data,4,4)!=checksum(data.substr(8)) || numberAt(data,8,4)!=id ||
         !numberAt(data,12,8) || std::binary_search(segments.begin(),segments.end(),id)){
        error="Unreadable history clear in "+filename;return false;
      }
      ClearScope scope{ClearKind(uint8_t(data[20])),data.substr(21)};
      if(!scope.valid()){error="Invalid history clear in "+filename;return false;}
      if(numberAt(data,12,8)==pendingClear)foundClear=true;
      auto& cutoff=scope.kind==ClearKind::Peer?peerCuts[scope.peer]:scope.kind==ClearKind::Aprs?aprsCut:commsCut;
      cutoff=std::max(id,cutoff);last=std::max(id,last);
      if(scope.kind==ClearKind::Peer)newestPeerCut=std::max(id,newestPeerCut);
      fs.yieldIo();
    }
    std::string header,body;
    for(auto id:segments){
      const auto filename=path(id);
      auto file=fs.openHistory(filename);
      if(!file){error="Cannot open "+filename;return false;}
      uint64_t remaining=file.size();
      unsigned count=0;
      while(remaining){
        if(remaining<12){++tornTails;break;}
        if(!readExact(file,header,12)){error="Read failed in "+filename;return false;}
        auto size=numberAt(header,4,4);
        const bool version2=header.compare(0,4,"HTM2")==0;
        if((!version2 && header.compare(0,4,"HTM1"))||size<(version2?36u:33u)||size>frameLimit-12){error="Invalid message header in "+filename;return false;}
        remaining-=12;
        if(size>remaining){++tornTails;break;}
        if(!readExact(file,body,size_t(size))){error="Read failed in "+filename;return false;}
        Record record;
        if(checksum(body)!=numberAt(header,8,4)||!decode(body,record,version2)){error="Damaged message record in "+filename;return false;}
        bool hidden=id<(record.isComms()?commsCut:aprsCut);
        if(!hidden && !record.isComms() && !record.mapOnly && id<newestPeerCut){
          const bool outgoing=!record.peer.empty();
          const auto thread=aprs::threadText(record.from,outgoing?record.peer:record.target,record.text,outgoing,record.directed);
          auto cut=peerCuts.find(thread.key);
          hidden=cut!=peerCuts.end() && id<cut->second;
        }
        if(!hidden)visit(std::move(record));
        remaining-=size;
        if(++count%64==0)fs.yieldIo();
      }
    }
    if(last==UINT32_MAX){error="Message segment numbers exhausted.";return false;}
    if(last){
      auto file=path(last,std::binary_search(segments.begin(),segments.end(),last)?"log":"clr");
      auto bytes=fs.size(file);
      if(bytes<0){error="Cannot verify last archive file.";return false;}
      size_t n=size_t(std::min<int64_t>(bytes,64));
      auto tail=n?fs.range(file,bytes-n,n):std::string{};
      if(tail.size()!=n){error="Cannot read the end of the archive.";return false;}
      rememberTip(last,std::move(file),bytes,std::move(tail));
    }else rememberTip(0,{},0,{});
    segment=last+1;offset=0;ready=true;return true;
  }
  template<class FS> bool clear(FS& fs,const ClearScope& scope,uint64_t token){
    if(!ready||!token||!scope.valid()){error="History must be ready to clear.";return false;}
    if(segment>=UINT32_MAX-1){error="Message segment numbers exhausted.";return false;}
    std::vector<uint32_t> logs,markers;
    bool listed=fs.historySegments(logs,markers);
    uint32_t newest=0;
    for(auto id:logs)newest=std::max(newest,id);
    for(auto id:markers)newest=std::max(newest,id);
    if(!listed || newest!=tipId || (tipId && (fs.size(tipPath)!=tipSize ||
       (!tipTail.empty() && fs.range(tipPath,tipSize-tipTail.size(),tipTail.size())!=tipTail)))){
      ready=false;error="Archive changed before clear. Reloading without applying it.";return false;
    }
    const uint32_t id=segment+(offset?1:0);
    const auto marker=path(id,"clr");
    const auto temporary=marker+"."+std::to_string(token)+".tmp";
    std::string body;number(body,id,4);number(body,token,8);body+=char(scope.kind);body+=scope.peer;
    std::string data="HTC1";number(data,checksum(body),4);data+=body;
    if(!fs.mkdir("/htcommander")||!fs.mkdir(folder)||fs.exists(path(id))||fs.exists(marker)||
       fs.exists(temporary)||!fs.append(temporary,data)||fs.size(temporary)!=int64_t(data.size())||
       fs.range(temporary,0,data.size())!=data||!fs.rename(temporary,marker)||
       fs.size(marker)!=int64_t(data.size())||fs.range(marker,0,data.size())!=data){
      ready=false;error="Could not verify history clear. Recovering the archive.";return false;
    }
    rememberTip(id,marker,data.size(),data.substr(data.size()-std::min<size_t>(64,data.size())));
    segment=id+1;offset=0;return true;
  }
  template<class FS> bool append(FS& fs,const Record& record){
    if(!ready){error="Message archive needs recovery.";return false;}
    auto frame=encode(record);
    if(frame.empty()){error="Message record cannot be encoded.";return false;}
    if(offset+frame.size()>segmentLimit){
      if(segment==UINT32_MAX){error="Message segment numbers exhausted.";return false;}
      ++segment;offset=0;
    }
    const auto filename=path(segment);
    if(!fs.mkdir("/htcommander")||!fs.mkdir(folder) ||
       (offset?fs.size(filename)!=int64_t(offset):(fs.exists(filename)||fs.exists(path(segment,"clr"))))){
      ready=false;error="Message archive changed or unavailable.";return false;
    }
    if(!fs.append(filename,frame)||fs.size(filename)!=int64_t(offset+frame.size())||fs.range(filename,offset,frame.size())!=frame){
      ready=false;error="Message write or readback failed.";return false;
    }
    offset+=frame.size();
    // Save a verified suffix from the current frame. Frames can be shorter
    // than 64 bytes, in which case just that complete frame is sufficient.
    rememberTip(segment,filename,offset,frame.substr(frame.size()-std::min<size_t>(64,frame.size())));
    return true;
  }
};
}
