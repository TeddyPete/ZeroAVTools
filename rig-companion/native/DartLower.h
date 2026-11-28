#pragma once
#include "MailSort.h"
#include "vendor/dart/CaseData.inc"
namespace dartcase {
inline uint32_t lowerPoint(uint32_t ch){
  if(ch<256){auto info=stage2_[ch];if((info&3)==2)ch+=info>>2;}
  else if(ch<sizeof(stage1_)*256){
    auto info=stage2_[size_t(stage1_[ch>>8])*256+(ch&255)];
    if((info&3)==2)ch+=info>>2;
    else if((info&3)==3)ch+=stage2_exception_[info>>2][1];
  }
  return ch;
}
inline void encode(std::string& out,uint32_t ch){
  if(ch<128)out+=char(ch);
  else if(ch<2048){out+=char(0xc0|(ch>>6));out+=char(0x80|(ch&63));}
  else if(ch<65536){out+=char(0xe0|(ch>>12));out+=char(0x80|((ch>>6)&63));out+=char(0x80|(ch&63));}
  else{out+=char(0xf0|(ch>>18));out+=char(0x80|((ch>>12)&63));out+=char(0x80|((ch>>6)&63));out+=char(0x80|(ch&63));}
}
inline std::string lower(const std::string& text){
  mailorder::Units units(text);unsigned ch=0;std::string out;out.reserve(text.size());
  while(units.next(ch)){
    if(ch>=0xd800 && ch<=0xdbff){unsigned tail=0;if(units.next(tail))ch=0x10000+((ch-0xd800)<<10)+(tail-0xdc00);}
    encode(out,lowerPoint(ch));
  }
  return out;
}
inline bool whitespace(unsigned ch){return (ch>=9 && ch<=13)||ch==32||ch==0x85||ch==0xa0||ch==0x1680||(ch>=0x2000&&ch<=0x200a)||ch==0x2028||ch==0x2029||ch==0x202f||ch==0x205f||ch==0x3000||ch==0xfeff;}
inline std::string trim(const std::string& text){
  // Keep original bytes between the first and last non-whitespace scalar.
  mailorder::Units units(text);size_t first=text.size(),last=0;unsigned ch=0;
  while(units.position()<text.size()){
    auto start=units.position();units.next(ch);
    if(ch>=0xd800 && ch<=0xdbff){unsigned tail;units.next(tail);}
    if(!whitespace(ch)){if(first==text.size())first=start;last=units.position();}
  }
  return first==text.size()?std::string{}:text.substr(first,last-first);
}
}
