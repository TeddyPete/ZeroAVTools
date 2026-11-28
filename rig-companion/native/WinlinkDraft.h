#pragma once
#include "WinlinkStore.h"
#include "DurableBook.h"
namespace winmail {
struct Draft {
  std::string mid,date,from,to,cc,subject,body,mbo,location;
  bool personal=false,p2p=false;
  struct File{std::string name;Bytes bytes;};
  std::vector<File> files;
  uint64_t generation=0;
};
inline Draft editMail(const StoredMail& mail){
  const auto& m=mail.message;Draft d;d.mid=m.mid;d.date=m.date;d.from=m.from;d.to=m.to;d.cc=m.cc;d.subject=m.subject;d.mbo=m.mbo;d.location=m.location;d.personal=m.personal;d.p2p=m.p2p;
  d.body.assign(mail.raw.begin()+m.body.offset,mail.raw.begin()+m.body.offset+m.body.size);
  for(auto& a:m.attachments)d.files.push_back({a.name,{mail.raw.begin()+a.data.offset,mail.raw.begin()+a.data.offset+a.data.size}});
  return d;
}
inline bool draftBytes(const Draft& d,Bytes& raw,std::string& error){
  raw.clear();
  if(!validMid(d.mid)){error="Missing message ID.";return false;}
  for(auto& value:{d.date,d.from,d.to,d.cc,d.subject,d.mbo,d.location})
    if(value.find_first_of("\r\n")!=value.npos){error="Mail headers cannot contain line breaks.";return false;}
  std::string headers="MID: "+d.mid+"\r\nDate: "+d.date+"\r\nFrom: "+d.from+"\r\nTo: "+d.to+
    "\r\nCc: "+d.cc+"\r\nSubject: "+d.subject+"\r\nBody: "+std::to_string(d.body.size())+"\r\n";
  if(d.personal)headers+="Type: Private\r\n";if(d.p2p)headers+="X-P2P: True\r\n";
  if(!d.mbo.empty())headers+="Mbo: "+d.mbo+"\r\n";if(!d.location.empty())headers+="X-Location: "+d.location+"\r\n";
  size_t size=headers.size()+d.body.size()+5;
  for(auto& f:d.files){
    if(f.name.find_first_of("\r\n")!=f.name.npos){error="Invalid attachment name.";return false;}
    headers+="File: "+std::to_string(f.bytes.size())+" "+f.name+"\r\n";size+=f.bytes.size()+f.name.size()+32;
  }
  if(size>1024*1024){error="Message and attachments exceed 1 MiB.";return false;}
  headers+="\r\n";raw.assign(headers.begin(),headers.end());raw.insert(raw.end(),d.body.begin(),d.body.end());
  raw.insert(raw.end(),{13,10});
  for(auto& f:d.files){raw.insert(raw.end(),f.bytes.begin(),f.bytes.end());raw.insert(raw.end(),{13,10});}
  raw.push_back(0);
  if(raw.size()>1024*1024){raw.clear();error="Message exceeds 1 MiB.";return false;}
  return true;
}
inline bool cleanRecipients(std::string input,std::string& output){
  output.clear();for(char& c:input)if(c==' ')c=';';
  auto alpha=[](char c){return (c>='a'&&c<='z')||(c>='A'&&c<='Z');};
  auto alnum=[&](char c){return alpha(c)||(c>='0'&&c<='9');};
  for(size_t pos=0;pos<input.size();){
    auto end=input.find(';',pos);if(end==input.npos)end=input.size();
    auto value=contactbook::trim(input.substr(pos,end-pos));pos=end+1;if(value.empty())continue;
    auto at=value.find('@');
    if(at==value.npos){
      if(value.size()>10)return false;auto dash=value.find('-');
      auto call=value.substr(0,dash);if(call.empty()||!std::all_of(call.begin(),call.end(),alnum))return false;
      if(dash!=value.npos){
        auto ssid=value.substr(dash+1);
        if(ssid.empty()||ssid.size()>2||!std::all_of(ssid.begin(),ssid.end(),[](char c){return c>='0'&&c<='9';}))return false;
      }
    }else{
      if(!at || at+1>=value.size())return false;
      auto dot=value.find_last_of('.');if(dot==value.npos || dot<=at || value.size()-dot<3)return false;
      for(size_t i=0;i<at;++i)if(!alnum(value[i]) && std::string("._%+-").find(value[i])==std::string::npos)return false;
      for(size_t i=at+1;i<dot;++i)if(!alnum(value[i]) && value[i]!='.'&&value[i]!='-')return false;
      for(size_t i=dot+1;i<value.size();++i)if(!alpha(value[i]))return false;
    }
    if(!output.empty())output+=';';output+=value;
  }
  return true;
}
inline constexpr size_t maxMailMetadata=64*1024;
struct Managed {StoredMail mail;uint64_t generation=0;char box='I';bool unread=false;std::string metadata;};
template<class FS> std::string readDraftFile(FS& fs,const std::string& path){
  auto n=fs.size(path);if(n<0 || n>1024*1024+maxMailMetadata+256)return {};
  std::string data;
  for(size_t offset=0;offset<size_t(n);){
    size_t count=std::min(size_t(4096),size_t(n)-offset);auto chunk=fs.range(path,offset,count);
    if(chunk.size()!=count)return {};data+=chunk;offset+=count;fs.yieldIo();
  }
  return data;
}
template<class FS>bool loadManaged(FS& fs,std::string mid,Managed& result,std::string& error){
  result={};error.clear();auto path=mailPath(mid);
  if(path.empty()){error="Invalid message ID.";return false;}
  if(fs.exists(path+".del")){error="Message deletion is pending. Reload the mailbox to finish cleanup.";return false;}
  auto a=durablebook::decode(readDraftFile(fs,path+".a")),b=durablebook::decode(readDraftFile(fs,path+".b"));
  if(a.valid || b.valid){
    auto& chosen=b.valid && (!a.valid || b.generation>a.generation)?b:a;
    size_t offset=chosen.body.size()>1 && (chosen.body[1]=='R' || chosen.body[1]=='U')?2:1;
    if(chosen.body.size()>offset && chosen.body[offset]=='@'){
      if(chosen.body.size()<offset+5){error="Truncated mailbox metadata.";return false;}
      auto count=history::numberAt(chosen.body,offset+1,4);offset+=5;
      if(count>maxMailMetadata || count>chosen.body.size()-offset){error="Invalid mailbox metadata size.";return false;}
      result.metadata=chosen.body.substr(offset,size_t(count));offset+=size_t(count);
    }
    if(chosen.body.empty() || std::string("IODSATC").find(chosen.body[0])==std::string::npos ||
       !unpack(chosen.body.substr(offset),result.mail,error) || result.mail.message.mid!=mid){error="Invalid mailbox state.";return false;}
    result.generation=chosen.generation;result.box=chosen.body[0];result.unread=chosen.body.size()>1 && chosen.body[1]=='U';return true;
  }
  if(fs.exists(path+".a")||fs.exists(path+".b")){error="Mailbox state unreadable. Previous files retained.";return false;}
  if(!loadMail(fs,mid,1024*1024,result.mail,error))return false;
  result.box=result.mail.inbox?'I':'O';result.unread=result.mail.inbox;return true;
}
template<class FS>bool publishManaged(FS& fs,const std::string& mid,const Bytes& raw,char box,uint64_t generation,std::string& error,int unread=-1,const std::string* metadata=nullptr){
  error.clear();
  auto path=mailPath(mid);Managed prior;
  if(path.empty() || std::string("IODSATC").find(box)==std::string::npos){error="Invalid message ID or folder.";return false;}
  if(fs.exists(path+".del")){error="Deletion pending. Reload the mailbox before changing it.";return false;}
  bool exists=fs.exists(path) || fs.exists(path+".a") || fs.exists(path+".b");
  if(exists && !loadManaged(fs,mid,prior,error))return false;
  bool nextUnread=unread<0?(exists?prior.unread:box=='I'):unread!=0;
  const std::string& nextMetadata=metadata?*metadata:prior.metadata;
  if(nextMetadata.size()>maxMailMetadata){error="Mail backup metadata exceeds 64 KiB.";return false;}
  // Retry after an ambiguous completion is idempotent.
  if(exists && prior.mail.raw==raw && prior.box==box && prior.unread==nextUnread && prior.metadata==nextMetadata)return true;
  if(prior.generation!=generation || prior.generation==UINT64_MAX){error="Message changed since the editor opened. Reload before saving.";return false;}
  auto a=durablebook::decode(readDraftFile(fs,path+".a"));
  std::string target=path+(a.valid && a.generation==prior.generation?".b":".a");
  std::string body(1,box);body+=nextUnread?'U':'R';
  if(!nextMetadata.empty()){body+='@';history::number(body,nextMetadata.size(),4);body+=nextMetadata;}
  body+=envelope(raw,box=='I' || box=='C');
  auto data=durablebook::encode(prior.generation+1,body),part=target+".part";
  if(!fs.mkdir("/rig-companion") || !fs.mkdir("/rig-companion/winlink") || !fs.mkdir("/rig-companion/winlink/mail")){error="Mailbox unavailable.";return false;}
  // The previously committed slot remains intact while replacing its older sibling.
  if(!fs.write(part.c_str(),data) || readDraftFile(fs,part)!=data){error="Mailbox write could not be verified.";return false;}
  if(fs.exists(target) && !fs.remove(target)){error="Could not replace the older mailbox revision.";return false;}
  if(!fs.rename(part,target) || readDraftFile(fs,target)!=data){error="Mailbox publish could not be verified.";return false;}
  return true;
}
template<class FS>bool changeManaged(FS& fs,const Managed& expected,char box,int unread,std::string& error){
  Managed prior;if(!loadManaged(fs,expected.mail.message.mid,prior,error))return false;
  bool desired=unread<0?expected.unread:unread!=0;
  if(prior.mail.raw==expected.mail.raw && prior.box==box && prior.unread==desired &&
     expected.generation<UINT64_MAX && prior.generation==expected.generation+1)return true;
  if(prior.mail.raw!=expected.mail.raw || prior.generation!=expected.generation || prior.box!=expected.box){
    error="Message changed. Reload before changing its folder or read state.";return false;
  }
  return publishManaged(fs,expected.mail.message.mid,prior.mail.raw,box,prior.generation,error,unread);
}
template<class FS>bool deleteManaged(FS& fs,const Managed& expected,std::string& error){
  auto path=mailPath(expected.mail.message.mid),marker=path+".del",part=marker+".part";
  if(path.empty()){error="Invalid message ID.";return false;}
  if(fs.exists(marker))return recoverDeleted(fs,expected.mail.message.mid,error);
  if(!fs.exists(path) && !fs.exists(path+".a") && !fs.exists(path+".b"))return true;
  Managed prior;if(!loadManaged(fs,expected.mail.message.mid,prior,error))return false;
  if(prior.box!='T' || expected.box!='T' || prior.generation!=expected.generation || prior.mail.raw!=expected.mail.raw || prior.generation==UINT64_MAX){
    error="Message changed or is not in Trash. Reload before deleting.";return false;
  }
  auto data=durablebook::encode(prior.generation+1,"HTWLDEL1"+expected.mail.message.mid);
  if(!fs.write(part.c_str(),data) || readDraftFile(fs,part)!=data){
    error="Deletion record could not be verified. Mail retained.";return false;
  }
  if(!fs.rename(part,marker) || readDraftFile(fs,marker)!=data){
    error="Deletion could not be published. Reload to check its state.";return false;
  }
  return recoverDeleted(fs,expected.mail.message.mid,error);
}
template<class FS>bool saveManaged(FS& fs,const Draft& draft,char box,std::string& error){
  error.clear();
  if(box!='D' && box!='O'){error="Invalid compose destination.";return false;}
  Bytes raw;if(!draftBytes(draft,raw,error))return false;
  return publishManaged(fs,draft.mid,raw,box,draft.generation,error);
}
}
