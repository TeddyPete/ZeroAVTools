#pragma once
#include "WinlinkMessage.h"
#include "MessageHistory.h"
#include "DurableBook.h"
namespace winmail {
enum class Saved {Stored,Duplicate,Conflict,Failed};
inline std::string mailPath(const std::string& mid){
  if(!validMid(mid))return {};
  static constexpr char digits[]="0123456789abcdef";
  std::string path="/rig-companion/winlink/mail/";
  for(unsigned char c:mid){path+=digits[c>>4];path+=digits[c&15];}
  return path+".mail";
}
// A verified deletion marker precedes any removal of committed content.
template<class FS>bool recoverDeleted(FS& fs,const std::string& mid,std::string& error){
  auto path=mailPath(mid);if(path.empty()){error="Invalid message ID.";return false;}
  auto marker=path+".del";if(!fs.exists(marker))return true;
  auto n=fs.size(marker);
  if(n<1 || n>256){error="Unreadable deletion record. Mail files retained.";return false;}
  auto encoded=fs.range(marker,0,size_t(n));auto saved=durablebook::decode(encoded);
  if(encoded.size()!=size_t(n) || !saved.valid || saved.body!="HTWLDEL1"+mid){
    error="Invalid deletion record. Mail files retained.";return false;
  }
  for(auto suffix:{"",".a",".b",".part",".a.part",".b.part",".del.part"}){
    auto file=path+suffix;
    if(fs.exists(file) && (!fs.remove(file) || fs.exists(file))){error="Deletion recorded; card cleanup incomplete. Reload Trash to retry.";return false;}
    fs.yieldIo();
  }
  if(!fs.remove(marker) || fs.exists(marker)){error="Deletion cleanup could not be confirmed. Reload Trash to retry.";return false;}
  return true;
}
struct StoredMail {
  Message message;
  Bytes raw;
  bool inbox=false;
};
inline std::string envelope(const Bytes& raw,bool inbox){
  std::string body(1,inbox?'I':'O');body.append(raw.begin(),raw.end());
  std::string data="HTWL1";history::number(data,body.size(),4);history::number(data,history::checksum(body),4);
  return data+body;
}
inline bool unpack(const std::string& data,StoredMail& mail,std::string& error){
  mail={};
  if(data.size()<14 || data.compare(0,5,"HTWL1") || history::numberAt(data,5,4)!=data.size()-13 ||
     history::numberAt(data,9,4)!=history::checksum(data.substr(13)) || (data[13]!='I' && data[13]!='O')){
    error="Damaged mailbox record.";return false;
  }
  Bytes raw(data.begin()+14,data.end());Message m;
  if(!parseMessage(raw,m,error))return false;
  mail.message=std::move(m);mail.raw=std::move(raw);mail.inbox=data[13]=='I';return true;
}
template<class FS>bool loadMail(FS& fs,std::string mid,size_t limit,StoredMail& mail,std::string& error){
  mail={};auto path=mailPath(mid);
  if(!path.empty() && fs.exists(path+".del")){error="Message deletion is pending. Reload the mailbox.";return false;}
  auto size=path.empty()?-1:fs.size(path);
  if(size<14 || uint64_t(size)>limit+uint64_t(14)){error="Mail record unavailable or exceeds configured limit.";return false;}
  std::string data;data.reserve(size_t(size));
  for(size_t offset=0;offset<size_t(size);){
    size_t n=std::min(size_t(4096),size_t(size)-offset);
    auto chunk=fs.range(path,offset,n);
    if(chunk.size()!=n){error="Mail read failed.";return false;}
    data+=chunk;offset+=n;fs.yieldIo();
  }
  if(!unpack(data,mail,error))return false;
  if(mail.message.mid!=mid){mail={};error="Message ID differs from its mailbox record.";return false;}
  return true;
}
template<class FS>Saved saveMail(FS& fs,const Bytes& raw,const std::string& expectedMid,
                                const std::string& local,size_t limit,std::string& error,bool forceInbox=false){
  error.clear();Message message;
  if(!recoverDeleted(fs,expectedMid,error))return Saved::Failed;
  if(raw.size()>limit || !parseMessage(raw,message,error)){
    if(error.empty())error="Mail size exceeds configured limit.";return Saved::Failed;
  }
  if(message.mid!=expectedMid){error="Proposal and decoded message IDs differ.";return Saved::Conflict;}
  const auto path=mailPath(expectedMid),part=path+".part";
  if(!fs.mkdir("/rig-companion") || !fs.mkdir("/rig-companion/winlink") || !fs.mkdir("/rig-companion/winlink/mail")){error="Mailbox directory unavailable.";return Saved::Failed;}
  if(fs.exists(path)){
    StoredMail previous;
    if(!loadMail(fs,expectedMid,limit,previous,error))return Saved::Failed;
    if(previous.raw!=raw){error="Existing message ID has different content.";return Saved::Conflict;}
    return Saved::Duplicate;
  }
  auto data=envelope(raw,forceInbox || addressedTo(message,local));
  int64_t size=fs.exists(part)?fs.size(part):0;
  if(size<0 || uint64_t(size)>data.size()){error="Invalid partial mail record.";return Saved::Failed;}
  // A retry continues only an identical verified prefix. Preserve conflicting data.
  for(size_t offset=0;offset<size_t(size);){
    size_t n=std::min(size_t(4096),size_t(size)-offset);
    if(fs.range(part,offset,n)!=data.substr(offset,n)){error="Partial mail differs from received data.";return Saved::Conflict;}
    offset+=n;fs.yieldIo();
  }
  for(size_t offset=size_t(size);offset<data.size();){
    auto chunk=data.substr(offset,4096);
    if(!fs.append(part,chunk) || fs.size(part)!=int64_t(offset+chunk.size()) ||
       fs.range(part,offset,chunk.size())!=chunk){error="Mail write was not verified.";return Saved::Failed;}
    offset+=chunk.size();fs.yieldIo();
  }
  if(fs.exists(path) || !fs.rename(part,path)){error="Mail publish failed. Retry to verify receipt.";return Saved::Failed;}
  StoredMail verified;
  if(!loadMail(fs,expectedMid,limit,verified,error) || verified.raw!=raw){error="Published mail readback failed.";return Saved::Failed;}
  return Saved::Stored;
}
}
