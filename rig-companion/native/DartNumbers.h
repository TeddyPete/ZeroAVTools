#pragma once
#include "DartLower.h"
#include <optional>
#include <limits>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
namespace dartnumber {
// Native 64-bit Dart int.tryParse without an explicit radix. Positive 0x
// accepts unsigned 64-bit bit patterns; a negative sign uses signed bounds.
inline std::optional<int64_t> integer(const std::string& input){
 auto text=dartcase::trim(input);if(text.empty())return {};
 size_t pos=0;bool negative=text[pos]=='-';if(negative||text[pos]=='+')++pos;
 unsigned radix=10;if(text.compare(pos,2,"0x")==0||text.compare(pos,2,"0X")==0){radix=16;pos+=2;}
 if(pos==text.size())return {};
 uint64_t limit=radix==16&&!negative?UINT64_MAX:negative?(uint64_t(1)<<63):uint64_t(INT64_MAX),value=0;
 for(;pos<text.size();++pos){
  char c=text[pos];unsigned digit=c>='0'&&c<='9'?unsigned(c-'0'):c>='a'&&c<='f'?unsigned(c-'a'+10):c>='A'&&c<='F'?unsigned(c-'A'+10):radix;
  if(digit>=radix||value>(limit-digit)/radix)return {};value=value*radix+digit;
 }
 if(negative)return value==(uint64_t(1)<<63)?INT64_MIN:-int64_t(value);
 int64_t result;std::memcpy(&result,&value,sizeof(result));return result;
}
inline std::optional<double> real(const std::string& input){
 auto text=dartcase::trim(input);if(text.empty())return {};size_t pos=0;
 bool negative=text[pos]=='-';if(negative||text[pos]=='+')++pos;
 if(text.substr(pos)=="Infinity")return negative?-INFINITY:INFINITY;
 if(text.substr(pos)=="NaN")return std::copysign(std::numeric_limits<double>::quiet_NaN(),negative?-1.:1.);
 auto digit=[](char c){return c>='0'&&c<='9';};size_t count=0;
 while(pos<text.size()&&digit(text[pos])){++pos;++count;}
 if(pos<text.size()&&text[pos]=='.'){++pos;while(pos<text.size()&&digit(text[pos])){++pos;++count;}}
 if(!count)return {};
 if(pos<text.size()&&(text[pos]=='e'||text[pos]=='E')){
  ++pos;if(pos<text.size()&&(text[pos]=='+'||text[pos]=='-'))++pos;
  size_t first=pos;while(pos<text.size()&&digit(text[pos]))++pos;if(first==pos)return {};
 }
 if(pos!=text.size())return {};
 char* end=nullptr;double value=std::strtod(text.c_str(),&end);
 return end==text.c_str()+text.size()?std::optional<double>(value):std::nullopt;
}
inline int64_t truncate(double value){
 if(!std::isfinite(value))throw std::domain_error("Nonfinite number cannot be converted to an integer.");
 if(value>=9223372036854775808.)return INT64_MAX;
 if(value<=-9223372036854775808.)return INT64_MIN;
 return int64_t(value);
}
}
