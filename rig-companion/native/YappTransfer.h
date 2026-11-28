#pragma once
// Protocol port of HTCommander radio/yapp_transfer.dart, commit 50434c0369b404cca1fc72d8a4c1a58aa75e9f9c.
// Original Copyright 2026 Ylian Saint-Hilaire, Apache License 2.0.
#include <algorithm>
#include <cstdint>
#include <deque>
#include <string>
#include <vector>
#include "DartUtf8.h"
#include "DartNumbers.h"

namespace yapp {
enum class State {Idle,Listen,Header,OpenReceive,Receive,WriteReceive,FinishReceive,Ready,HeaderAck,ReadSource,DataAck,EofAck,EotAck,CancelAck};
struct File {std::string name,data,key;uint64_t size=0;std::string cachedKey;bool savePrompted=false;};
inline std::string baseName(std::string name){
  std::replace(name.begin(),name.end(),'\\','/');auto p=name.find_last_of('/');
  if(p!=std::string::npos)name=name.substr(p+1);
  return name.empty()?"file.bin":name;
}
inline bool filePath(const std::string& path){
  std::string folded=path;for(auto& c:folded)if(c>='A'&&c<='Z')c+=32;
  if(path.size()<2||path[0]!='/'||path.back()=='/'||folded=="/htcommander"||folded.rfind("/htcommander/",0)==0)return false;
  size_t start=1;
  for(size_t i=1;i<=path.size();++i){
    if(i==path.size()||path[i]=='/'){auto part=path.substr(start,i-start);if(part.empty()||part.back()=='.'||part.back()==' ')return false;start=i+1;}
    else if((unsigned char)path[i]<32||std::string("\\:*?\"<>|").find(path[i])!=std::string::npos)return false;
  }
  return true;
}
inline std::string suggestedSavePath(std::string name){
  name=baseName(std::move(name));for(auto& c:name)if((unsigned char)c<32||c==127||std::string(":*?\"<>|").find(c)!=std::string::npos)c='_';
  while(!name.empty()&&(name.back()=='.'||name.back()==' '))name.pop_back();
  if(name.empty())name="file.bin";std::string path="/"+name;return filePath(path)?path:"/received.bin";
}
class Transfer {
  std::string source,buffer;
  bool checksum=false,streamingSource=false;
  uint32_t sourceRequest=0;
  uint32_t sinkRequest=0;
  uint32_t ramFileId=0;bool cardReceive=false;
  std::string spoolKey;
  uint32_t since=0;
  unsigned retries=0;
  void stage(State next,uint32_t now){state=next;since=now;}
  void emit(std::initializer_list<unsigned char> bytes){std::string s;for(auto b:bytes)s+=char(b);tx.push_back(std::move(s));}
  void reason(unsigned char type,const std::string& why){auto s=why.substr(0,240);tx.push_back(std::string(1,char(type))+char(s.size())+s);}
  void fail(const std::string& why,uint32_t now){error=why;source.clear();buffer.clear();streamingSource=false;++sourceRequest;++sinkRequest;stage(sending?State::Idle:State::Listen,now);sending=false;retries=0;}
  void header(uint32_t now){
    std::string data=filename;data+='\0';data+=std::to_string(size);data+='\0';
    tx.push_back(std::string(1,'\x01')+char(data.size())+data);stage(State::HeaderAck,now);
  }
  void emitBlock(const std::string& data,uint32_t now){
    size_t n=data.size();unsigned char sum=0;for(unsigned char b:data)sum+=b;
    std::string packet=std::string(1,'\x02')+char(n)+data;if(checksum)packet+=char(sum);
    tx.push_back(std::move(packet));transferred+=n;stage(State::DataAck,now);
  }
  void block(uint32_t now){
    if(transferred==size){emit({3});stage(State::EofAck,now);return;}
    if(streamingSource){++sourceRequest;stage(State::ReadSource,now);return;}
    emitBlock(source.substr(size_t(transferred),size_t(std::min(uint64_t(128),uint64_t(size)-transferred))),now);
  }
  bool beginSend(std::string name,uint64_t length,bool streaming,uint32_t now){
    if(busy()){error="A transfer is already active";return false;}
    name=baseName(name);
    if(length>maxStreamBytes || name.size()+std::to_string(length).size()+4>256 || name.find('\0')!=std::string::npos){error="File exceeds filesystem limit or filename is too long";return false;}
    filename=std::move(name);size=static_cast<int64_t>(length);transferred=0;checksum=useChecksum;streamingSource=streaming;sending=true;error.clear();sentFile.clear();retries=0;
    emit({5,1});stage(State::Ready,now);return true;
  }
  void receiveHeader(const std::string& packet,uint32_t now){
    auto bad=[&](const char* message){reason(0x15,message);fail(message,now);};
    if(packet.size()<2){bad("Invalid header packet");return;}
    const size_t end=2+uint8_t(packet[1]);
    if(packet.size()<end){bad("Incomplete header packet");return;}
    // _parseNullSeparated ignores empty byte fields and accepts an unterminated
    // final field. Ignore bytes outside the declared header, as upstream does.
    std::string fields[2];unsigned field=0;
    for(size_t at=2;at<end&&field<2;){
      auto first=at;while(at<end&&packet[at])++at;
      if(at>first)fields[field++]=dartutf8::decode(std::string_view(packet.data()+first,at-first));
      if(at<end)++at;
    }
    if(field<2){bad("Invalid header format");return;}
    auto parsed=dartnumber::integer(fields[1]);
    if(!parsed){bad("Invalid file size");return;}
    auto name=baseName(std::move(fields[0]));
    filename=std::move(name);size=*parsed;transferred=0;buffer.clear();spoolKey.clear();checksum=useChecksum;sending=false;cardReceive=spoolReceive;
    if(spoolReceive){++sinkRequest;stage(State::OpenReceive,now);return;}
    emit({6,static_cast<unsigned char>(checksum?6:2)});stage(State::Receive,now);
  }
public:
  State state=State::Idle;
  bool useChecksum=true,sending=false,spoolReceive=false;
  static constexpr size_t maxFileBytes=2*1024*1024;
  // The Tab5 SD/File API uses 32-bit positions. Receive buffering is a separate,
  // temporary bound; a streamed source does not allocate its declared length.
  static constexpr uint64_t maxStreamBytes=0xffffffffull;
  uint32_t timeoutMs=60000;
  unsigned maxRetries=3;
  int64_t size=0;uint64_t transferred=0; // Header size is advisory; received bytes are counted independently.
  std::string filename,error,sentFile;
  std::deque<std::string> tx;
  std::deque<File> completed;
  size_t leasedRam=0; // A save worker can own the RAM buffer without copying it on the UI task.
  bool busy()const{return state!=State::Idle && state!=State::Listen;}
  void listen(){if(state==State::Idle)state=State::Listen;}
  void reset(){state=State::Idle;sending=false;streamingSource=false;++sourceRequest;++sinkRequest;source.clear();buffer.clear();spoolKey.clear();tx.clear();retries=0;size=transferred=0;filename.clear();error.clear();sentFile.clear();}
  bool send(std::string name,std::string data,uint32_t now){
    if(data.size()>maxFileBytes){error="Buffered file exceeds 2 MiB";return false;}
    if(!beginSend(std::move(name),data.size(),false,now))return false;
    source=std::move(data);return true;
  }
  bool sendStream(std::string name,uint64_t length,uint32_t now){
    if(!beginSend(std::move(name),length,true,now))return false;
    source.clear();return true;
  }
  bool needsSource()const{return streamingSource && busy();}
  bool readPending()const{return state==State::ReadSource;}
  uint32_t readId()const{return sourceRequest;}
  size_t readLength()const{return readPending()?size_t(std::min(uint64_t(128),uint64_t(size)-transferred)):0;}
  bool supplySource(uint32_t request,const std::string& data,bool success,uint32_t now){
    if(!readPending()||request!=sourceRequest)return false;
    if(!success||data.size()!=readLength()){cancel("Source file read failed or changed; transfer cancelled",now);return true;}
    emitBlock(data,now);return true;
  }
  bool sinkPending()const{return state==State::OpenReceive||state==State::WriteReceive||state==State::FinishReceive;}
  bool needsSink()const{return cardReceive&&(sinkPending()||state==State::Receive);}
  bool receivingInRam()const{return spoolReceive&&!cardReceive&&state==State::Receive;}
  bool ramFits(size_t incoming)const{
    size_t remaining=maxFileBytes;
    auto take=[&](size_t n){if(n>remaining)return false;remaining-=n;return true;};
    if(!take(leasedRam)||!take(buffer.size())||!take(incoming))return false;
    for(const auto& file:completed)if(!take(file.data.size()))return false;
    return true;
  }
  int progressPercent()const{return size>0?int(std::min<uint64_t>(100,100*transferred/uint64_t(size))):0;}
  bool useRamFallback(uint32_t request,uint32_t now){
    if(state!=State::OpenReceive||request!=sinkRequest)return false;
    if(!ramFits(0))return false;
    cardReceive=false;emit({6,static_cast<unsigned char>(checksum?6:2)});stage(State::Receive,now);return true;
  }
  uint32_t sinkId()const{return sinkRequest;}
  const std::string& sinkData()const{return buffer;}
  bool supplySink(uint32_t request,bool success,const std::string& key,uint32_t now){
    if(!sinkPending()||request!=sinkRequest)return false;
    if(state!=State::OpenReceive&&key!=spoolKey)return false;
    if(!success){cancel("microSD receive storage failed; file not acknowledged",now);return true;}
    if(state==State::OpenReceive){
      if(key.empty()){cancel("Receive storage returned no file",now);return true;}
      spoolKey=key;emit({6,static_cast<unsigned char>(checksum?6:2)});stage(State::Receive,now);
    }else if(state==State::WriteReceive){transferred+=buffer.size();buffer.clear();emit({6,1});stage(State::Receive,now);}
    else{completed.push_back({filename,{},spoolKey,transferred});spoolKey.clear();emit({6,3});stage(State::Header,now);}
    return true;
  }
  void cancel(const std::string& why,uint32_t now){
    if(!busy())return;
    tx.clear();reason(0x18,why);error=why;source.clear();buffer.clear();streamingSource=false;++sourceRequest;++sinkRequest;retries=0;stage(State::CancelAck,now);
  }
  void tick(uint32_t now){
    if(!busy()||uint32_t(now-since)<timeoutMs)return;
    since=now;
    // Upstream relies on AX.25 retransmission; its YAPP timer waits three
    // additional intervals rather than replaying blocks without sequence IDs.
    if(retries++<maxRetries)return;
    if(state==State::CancelAck){fail("YAPP cancel acknowledgment timed out",now);return;}
    cancel("Timeout - transfer aborted",now);
  }
  bool accept(const std::string& packet,uint32_t now){
    if(packet.empty())return false;
    unsigned char type=packet[0];
    auto sub=[&](unsigned char value){return packet.size()>=2 && (unsigned char)packet[1]==value;};
    if(state==State::Idle){if(type!=5||!sub(1))return false;listen();}
    if(state==State::Listen && type!=5&&type!=1&&type!=2&&type!=3&&type!=4&&type!=6&&type!=0x15&&type!=0x18)return false;
    since=now;
    if(type==0x18){
      if(state==State::CancelAck){emit({6,5});return true;}
      std::string why="Transfer cancelled by remote";
      if(packet.size()>2 && uint8_t(packet[1])>0 && packet.size()>=size_t(2+uint8_t(packet[1])))
        why=dartutf8::decode(std::string_view(packet.data()+2,uint8_t(packet[1])));
      tx.clear();emit({6,5});fail("Remote cancelled: "+why,now);return true;
    }
    switch(state){
      case State::Idle:break;
      case State::Listen:case State::Header:
        if(type==5&&sub(1)){emit({6,1});stage(State::Header,now);}
        else if(type==1)receiveHeader(packet,now);
        else if(type==4){emit({6,4});stage(State::Listen,now);retries=0;}
        break;
      case State::Receive:
        if(type==2){
          if(packet.size()<2){cancel("Invalid YAPP data length",now);break;}
          size_t n=(unsigned char)packet[1];if(!n)n=256;
          if(packet.size()<n+2+(checksum?1:0)){cancel("Invalid YAPP data length",now);break;}
          unsigned char sum=0;for(size_t i=2;i<n+2;++i)sum+=(unsigned char)packet[i];
          if(checksum && sum!=(unsigned char)packet[n+2]){cancel("YappC checksum error",now);break;}
          if(transferred>maxStreamBytes||n>maxStreamBytes-transferred){cancel("Received file exceeds the 32-bit filesystem limit",now);break;}
          if(!cardReceive&&!ramFits(n)){cancel("RAM receive limit reached. Save pending files or insert a card.",now);break;}
          if(cardReceive){buffer=packet.substr(2,n);++sinkRequest;stage(State::WriteReceive,now);}
          else{buffer.append(packet,2,n);transferred+=n;emit({6,1});}
        }else if(type==3){
          if(cardReceive){++sinkRequest;stage(State::FinishReceive,now);}
          else{completed.push_back({filename,std::move(buffer),"ram:"+std::to_string(++ramFileId),transferred});buffer.clear();emit({6,3});stage(State::Header,now);}
        }else if(type==4){
          emit({6,4});buffer.clear();spoolKey.clear();++sinkRequest;stage(State::Listen,now);retries=0;
        }
        break;
      case State::Ready:
        if(type==6&&sub(1))header(now);
        else if(type==0x15)fail("Receiver not ready",now);
        break;
      case State::HeaderAck:
        if(type==6&&(sub(6)||sub(2))){checksum=sub(6);block(now);}
        else if(type==0x15)fail("Receiver rejected file",now);
        break;
      case State::DataAck:
        if(type==6)block(now);
        else if(type==0x15)fail("Receiver reported transfer error",now);
        break;
      case State::ReadSource:break; // The worker supplies one block asynchronously.
      case State::OpenReceive:case State::WriteReceive:case State::FinishReceive:
        cancel("Peer sent data before receive storage acknowledgment",now);break;
      case State::EofAck:if(type==6&&sub(3)){emit({4});stage(State::EotAck,now);}break;
      case State::EotAck:if(type==6&&sub(4)){sentFile=filename;source.clear();streamingSource=false;sending=false;stage(State::Idle,now);retries=0;}break;
      case State::CancelAck:if(type==6&&sub(5)){sending=false;stage(State::Idle,now);retries=0;}break;
    }
    return true;
  }
};
}
