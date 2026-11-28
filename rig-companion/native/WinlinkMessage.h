#pragma once
#include "WinlinkBlocks.h"
#include "ContactBook.h"
#include <limits>
namespace winmail {
struct Slice {size_t offset=0,size=0;};
struct Attachment {std::string name;Slice data;};
struct Message {
  std::string mid,date,from,to,cc,subject,mbo,location;
  bool personal=false,p2p=false;
  Slice body;
  std::vector<Attachment> attachments;
};
inline bool lengthValue(const std::string& text,size_t& value){
  value=0;if(text.empty())return false;
  for(char c:text){
    if(c<'0'||c>'9'||value>(SIZE_MAX-unsigned(c-'0'))/10)return false;
    value=value*10+unsigned(c-'0');
  }
  return true;
}
inline bool validMid(const std::string& mid){
  if(mid.empty() || mid.size()>64)return false;
  for(unsigned char c:mid)if(c<33 || c>126)return false;
  return true;
}
inline bool parseMessage(const Bytes& raw,Message& result,std::string& error){
  result={};error.clear();Message m;
  // Every length describes bytes, including UTF-8 text and binary attachments.
  const uint8_t delimiter[]={13,10,13,10};
  auto it=std::search(raw.begin(),raw.end(),std::begin(delimiter),std::end(delimiter));
  if(it==raw.end()){error="Missing mail header separator.";return false;}
  size_t boundary=it-raw.begin(),pos=boundary+4;
  size_t bodySize=0;bool bodySeen=false,midSeen=false;
  for(size_t start=0;start<boundary;){
    auto end=std::find(raw.begin()+start,raw.begin()+boundary,uint8_t(10))-raw.begin();
    std::string line(raw.begin()+start,raw.begin()+end);start=size_t(end)+1;
    auto colon=line.find(':');if(colon==line.npos)continue;
    auto key=contactbook::trim(line.substr(0,colon)),value=contactbook::trim(line.substr(colon+1));
    for(char& c:key)if(c>='A'&&c<='Z')c+=32;
    if(key=="mid"){
      if(midSeen && m.mid!=value){error="Conflicting message IDs.";return false;}
      m.mid=value;midSeen=true;
    }else if(key=="body"){
      if(bodySeen || !lengthValue(value,bodySize)){error="Invalid body length.";return false;}bodySeen=true;
    }else if(key=="file"){
      auto space=value.find(' ');size_t count;
      if(!lengthValue(value.substr(0,space),count)){
        error="Invalid attachment header.";return false;
      }
      m.attachments.push_back({space==value.npos?"":contactbook::trim(value.substr(space+1)),{0,count}});
    }else if(key=="to" || key=="cc"){
      auto& recipients=key=="to"?m.to:m.cc;
      if(!recipients.empty() && !value.empty())recipients+=';';
      recipients+=value;
    }else if(key=="date")m.date=value;
    else if(key=="from")m.from=value;
    else if(key=="subject")m.subject=value;
    else if(key=="mbo")m.mbo=value;
    else if(key=="x-location")m.location=value;
    else if(key=="type")m.personal=value=="Private" || value=="private";
    else if(key=="x-p2p")m.p2p=value=="True" || value=="true";
  }
  if(!validMid(m.mid)){error="Missing or unsupported message ID.";return false;}
  auto consume=[&](size_t count,Slice& slice){
    if(count>raw.size()-pos || raw.size()-pos-count<2)return false;
    slice={pos,count};pos+=count;
    if(raw[pos]!=13 || raw[pos+1]!=10)return false;
    pos+=2;return true;
  };
  if(!consume(bodySize,m.body)){error="Truncated mail body or separator.";return false;}
  for(auto& attachment:m.attachments){
    if(!consume(attachment.data.size,attachment.data)){error="Truncated attachment or separator.";return false;}
  }
  if(pos+1!=raw.size() || raw[pos]!=0){error="Invalid mail end marker or trailing bytes.";return false;}
  result=std::move(m);return true;
}
inline bool addressedTo(const Message& m,std::string station){
  auto upper=[](std::string value){for(auto& c:value)if(c>='a'&&c<='z')c-=32;return value;};
  station=upper(station);if(station.empty())return false;
  for(const auto* group:{&m.to,&m.cc}){
    for(size_t start=0;start<group->size();){
      auto end=group->find(';',start);if(end==group->npos)end=group->size();
      auto recipient=upper(group->substr(start,end-start));
      auto at=recipient.find('@');
      if(at==recipient.npos){
        if(recipient==station || recipient.rfind(station+"-",0)==0)return true;
      }else{
        auto key=recipient.substr(0,at);
        if((recipient.substr(at+1)=="WINLINK.ORG" && key==station) || key.rfind(station+"-",0)==0)return true;
      }
      start=end+1;
    }
  }
  return false;
}
}
