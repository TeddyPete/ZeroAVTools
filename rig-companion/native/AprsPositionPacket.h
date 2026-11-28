#pragma once
#include "AprsPosition.h"
#include "ContactBook.h"
#include <cstdint>
namespace aprs {
inline bool digits(const std::string& s,size_t start,size_t n){
  return start<=s.size() && n<=s.size()-start &&
    std::all_of(s.begin()+start,s.begin()+start+n,[](char c){return c>='0'&&c<='9';});
}
inline void stripTelemetry(std::string& text){
  size_t start=text.find('|'),best=text.npos,last=0;
  while(start!=text.npos){
    auto end=text.find('|',start+1);if(end==text.npos)break;
    auto len=end-start-1;
    if(len>=4 && len<=14 && !(len%2) && std::all_of(text.begin()+start+1,text.begin()+end,[](unsigned char c){return c>=33&&c<=123;})){
      best=start;last=end;
    }
    start=end;
  }
  if(best!=text.npos){text.erase(best,last-best+1);text=contactbook::trim(text);}
}
inline bool micE(const std::string& dest,const std::string& body,aprs::Position& pos,std::string& comment){
  std::string call=dest.substr(0,dest.find('-'));if(call.size()!=6 || body.size()<3)return false;
  bool custom=call.substr(0,3).find_first_of("ABCDEFGHIJK")!=call.npos;
  int digit[6];
  for(int i=0;i<6;++i){
    char ch=call[i];bool numeric=ch>='0' && ch<='9';
    bool allowed=i<3&&custom?(ch>='A'&&ch<='L'):(ch=='L'||(ch>='P'&&ch<='Z'));
    if(!numeric&&!allowed)return false;
    int d=ch-'0';if(ch=='L')d=10;else if(ch>='A'&&ch<='K')--d;
    digit[i]=(d&15)==10?0:d&15;
  }
  int deg=uint8_t(body[0])-28,min=uint8_t(body[1])-28,frac=uint8_t(body[2])-28;
  if(deg<0||deg>99||min<0||min>99||frac<0||frac>99)return false;
  if(call[4]>='P')deg+=100;if(deg>=190)deg-=190;else if(deg>=180)deg-=80;
  if(min>=60)min-=60;
  pos.lat=(call[3]>='P'?1:-1)*(digit[0]*10+digit[1]+(digit[2]*10+digit[3])/60.0+(digit[4]*10+digit[5])/6000.0);
  pos.lon=(call[5]>='P'?-1:1)*(deg+min/60.0+frac/6000.0);
  comment=body.size()>8?body.substr(8):std::string{};
  if(comment.size()>=4&&comment[3]=='}')comment.erase(0,4);
  else if(comment.size()>=5 && (comment[0]=='>'||comment[0]==']')&&comment[4]=='}')comment.erase(0,5);
  comment=contactbook::trim(comment);return true;
}

struct PositionPacket{
 Position point{};std::string comment,information,weatherText;
 char symbolTable=0,symbolCode=0;bool micE=false,hasSymbolTable=false,hasSymbolCode=false;
 int course=0,speed=0,ambiguity=0;int64_t altitude=0;
};
inline bool positionBody(char type,const std::string& body,const std::string& destination,PositionPacket& out){
 PositionPacket parsed;
 if(type==0x1c||type==0x1d||type==0x27||type==0x60){
  if(!micE(destination,body,parsed.point,parsed.comment))return false;parsed.micE=true;
  if(body.size()>6){parsed.symbolCode=body[6];parsed.hasSymbolCode=true;}if(body.size()>7){parsed.symbolTable=body[7];parsed.hasSymbolTable=true;}
  size_t altitudeStart=0;
  if(body.size()>=12&&body[11]=='}')altitudeStart=8;
  else if(body.size()>=13&&(body[8]=='>'||body[8]==']')&&body[12]=='}')altitudeStart=9;
  if(altitudeStart){
   int a=uint8_t(body[altitudeStart])-33,b=uint8_t(body[altitudeStart+1])-33,c=uint8_t(body[altitudeStart+2])-33;
   if(a>=0&&a<=90&&b>=0&&b<=90&&c>=0&&c<=90)parsed.altitude=std::llround((a*91*91+b*91+c-10000)*3.28084);
  }
  if(body.size()>5){
   int m=uint8_t(body[4])-28,s=uint8_t(body[3])-28,d=uint8_t(body[5])-28;
   if(m>=0&&m<=97&&s>=0&&s<=99&&d>=0&&d<=99){
    int speed=s*10+m/10,course=(m%10)*100+d;if(speed>=800)speed-=800;if(course>=400)course-=400;
    if(course>0){parsed.course=course;parsed.speed=speed;}
   }
  }
 }else if(type=='!'||type=='='||type=='/'||type=='@'){
  size_t start=(type=='/'||type=='@')?7:0;if(body.size()<=start)return false;
  bool plain=digits(body,start,1);size_t length=plain?19:13;if(body.size()-start<length)return false;
  auto encoded=body.substr(start,length);
  if(plain){parsed.ambiguity=int(std::count(encoded.begin(),encoded.begin()+8,' '));for(int index:{2,3,5,6,12,13,15,16})if(encoded[index]==' ')encoded[index]='0';}
  if(!position("!"+encoded,parsed.point))return false;
  parsed.symbolTable=encoded[plain?8:0];parsed.symbolCode=encoded[plain?18:9];parsed.hasSymbolTable=parsed.hasSymbolCode=true;
  if(!plain&&parsed.symbolTable>='a'&&parsed.symbolTable<='j')parsed.symbolTable=char(parsed.symbolTable-'a'+'0');
  parsed.comment=body.substr(start+length);
  if(plain){
   auto number=[&](size_t start,size_t length){int value=0;for(size_t i=0;i<length;++i)value=value*10+parsed.comment[start+i]-'0';return value;};
   if(digits(parsed.comment,0,3)&&parsed.comment.size()>=7&&parsed.comment[3]=='/'&&digits(parsed.comment,4,3)){
    parsed.course=number(0,3);parsed.speed=number(4,3);parsed.comment.erase(0,7);
   }
   if(parsed.comment.rfind("/A=",0)==0&&digits(parsed.comment,3,6)){parsed.altitude=number(3,6);parsed.comment.erase(0,9);}
  }else{
   int c=uint8_t(encoded[10]),s=uint8_t(encoded[11]),t=uint8_t(encoded[12]);
   if(c==0x20){}
   else if(((t-33)&0x18)==0x10)parsed.altitude=std::llround(std::pow(1.002,(c-33)*91+s-33));
   else if(c==0x7b){}
   else if(c>=0x21&&c<=0x7a){parsed.course=(c-33)*4;parsed.speed=int(std::llround(std::pow(1.08,s-33)-1));}
  }
 }else return false;
 if(!std::isfinite(parsed.point.lat)||!std::isfinite(parsed.point.lon)||std::abs(parsed.point.lat)>90||std::abs(parsed.point.lon)>180)return false;
 if(!parsed.micE&&parsed.symbolCode=='_')parsed.weatherText=parsed.comment;
 stripTelemetry(parsed.comment);parsed.information=std::string(1,type)+body;out=std::move(parsed);return true;
}
inline bool positionPacket(const std::string& information,const std::string& destination,PositionPacket& out){
 if(information.empty())return false;auto data=information;
 // Match the pinned parser's single third-party '*:' wrapper handling.
 // Position attribution and Mic-E destination still use the outer AX.25 packet.
 if(data.front()=='}'){auto end=data.find("*:");if(end==data.npos||end<1)return false;data.erase(0,end+2);if(data.empty())return false;}
 char type=data[0];auto body=data.substr(1);
 auto signature=body.rfind('}');
 if(signature!=body.npos&&(signature+7==body.size()||(signature+7<body.size()&&body[signature+7]=='{')))body.erase(signature,7);
 return positionBody(type,body,destination,out);
}

}
