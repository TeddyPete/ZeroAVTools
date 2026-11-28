#pragma once
#include <string>
#include <cmath>
namespace aprs {
struct Position { double lat,lon; };
inline bool coordinate(const std::string &s,int degreeDigits,double &out){
  const int n=degreeDigits;
  if(s.size()!=size_t(n+6)||s[n+2]!='.')return false;
  int degrees=0,minutes=0;
  for(int i=0;i<n+5;++i){
    if(i==n+2)continue;
    if(s[i]<'0'||s[i]>'9')return false;
    if(i<n)degrees=10*degrees+s[i]-'0';else minutes=10*minutes+s[i]-'0';
  }
  const char sign=s.back();
  if(n==2?(sign!='N'&&sign!='S'):(sign!='E'&&sign!='W'))return false;
  if(minutes>=6000)return false;
  out=degrees+minutes/6000.0;
  if(out>(n==2?90:180))return false;
  if(sign=='S'||sign=='W')out=-out;
  return true;
}
inline bool position(const std::string &info,Position &p){
  if(info.empty())return false;
  const char type=info.front();
  if(type!='!'&&type!='='&&type!='@'&&type!='/')return false;
  size_t i=(type=='@'||type=='/')?8:1;
  if(info.size()<i+13)return false;
  if(info[i]>='0'&&info[i]<='9'){
    return info.size()>=i+19 && coordinate(info.substr(i,8),2,p.lat) && coordinate(info.substr(i+9,9),3,p.lon);
  }
  int v[2]={};
  for(int j=0;j<8;++j){
    int digit=int(info[i+1+j])-33;
    if(digit<0||digit>=91)return false;
    v[j/4]=91*v[j/4]+digit;
  }
  p.lat=90-v[0]/380926.0;p.lon=-180+v[1]/190463.0;
  return std::abs(p.lat)<=90&&std::abs(p.lon)<=180;
}
}
