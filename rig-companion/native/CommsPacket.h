#pragma once
#include "AprsRadioMessage.h"
#include "AprsPositionPacket.h"
#include "ContactBook.h"
#include <cstdio>
#include <cstdlib>

namespace comms {
enum class Type {None,Ax25,Aprs};
struct Metadata{
  Type type=Type::None;
  aprs::Position point{};
  bool located()const{return point.lat!=0 || point.lon!=0;}
  bool valid()const{return (type==Type::Aprs||type==Type::Ax25) && std::isfinite(point.lat)&&std::isfinite(point.lon)&&
    std::abs(point.lat)<=90&&std::abs(point.lon)<=180;}
};
inline std::string coordinate(double value){
  char out[32];std::snprintf(out,sizeof(out),"%.15g",value);return out;
}
inline const char* name(Type t){return t==Type::Aprs?"APRS":"AX.25";}
inline std::string route(const std::string& channel,const char* kind,const std::string& source,const std::string& target){
  auto label=(channel.empty()?std::string{}:"["+channel+"] ")+kind;
  if(!source.empty()){label+=" "+source;if(!target.empty())label+=" > "+target;}
  if(!channel.empty()&&std::string(kind)=="Ident")label+=" ⚑";
  return label;
}
struct Entry{Metadata meta;std::string source,target,text;};

// Decode malformed UTF-8 as replacement characters, as the upstream AX.25
// decoder does. Show embedded NUL explicitly on a C-string based display.
inline std::string displayText(const std::string& data,bool visibleNul=true){
  std::string s;
  for(size_t i=0;i<data.size();){
    unsigned c=uint8_t(data[i]);size_t n=c<128?1:c>=194&&c<=223?2:c>=224&&c<=239?3:c>=240&&c<=244?4:0;
    if(n>1 && i+1<data.size()){
      unsigned b=uint8_t(data[i+1]);
      if((c==224&&b<160)||(c==237&&b>=160)||(c==240&&b<144)||(c==244&&b>=144))n=0;
    }
    size_t len=1;
    while(len<n && i+len<data.size() && (uint8_t(data[i+len])&192)==128)++len;
    if(n==0 || len<n)s+="\xef\xbf\xbd";
    else if(!c && visibleNul)s+="\xe2\x90\x80";
    else s.append(data,i,len);
    i+=len;
  }
  return s;
}
using aprs::digits;using aprs::stripTelemetry;using aprs::micE;
inline Entry decode(const benshi::Decoded& p){
  if(!p.ok || (p.control&0xef)!=3 || p.info.empty())return {};
  Entry e{{Type::Ax25,{}},p.src,p.dest,displayText(p.info)};
  auto data=p.info;
  if(data.front()=='}'){
    auto end=data.find("*:");if(end==data.npos || end<1)return e;
    data.erase(0,end+2);if(data.empty())return e;
  }
  const char type=data[0];auto body=data.substr(1);
  auto sig=body.rfind('}');
  if(sig!=body.npos && (sig+7==body.size() || (sig+7<body.size()&&body[sig+7]=='{')))body.erase(sig,7);
  if(type==':'){
    if(body.size()<10)return e;
    auto to=contactbook::trim(body.substr(0,9));for(char& c:to)if(c>='a'&&c<='z')c-=32;
    auto text=body.substr(10),prefix=text.substr(0,3);for(char& c:prefix)if(c>='a'&&c<='z')c-=32;
    if(text.size()>3 && (prefix=="ACK"||prefix=="REJ"))return e;
    text=text.substr(0,text.rfind('{'));
    if(text.empty())return e;
    if(text.rfind("NWS_",0)==0)for(size_t i=0;(i=text.find("NWS_",i))!=text.npos;i+=4)text[i+3]='-';
    e.meta.type=Type::Aprs;e.target=to;e.text=displayText(text);return e;
  }
  aprs::PositionPacket parsed;if(!aprs::positionBody(type,body,p.dest,parsed)||parsed.comment.empty())return e;
  Metadata meta{Type::Aprs,parsed.point};if(!meta.valid())return e;
  e.meta=meta;e.target.clear();e.text=displayText(parsed.comment);return e;
}
}
