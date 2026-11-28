#pragma once
#include <string>
#include <cmath>
#include <cstdint>
#include <cstring>
namespace dartformat {
// Exact IEEE-754 rounding for the 0..2 decimal places / bounded magnitudes
// used by weather and map distance labels. Dart rounds decimal halfway values away from zero;
// printf's ties-to-even and round(value*scale) both differ for some inputs.
inline std::string fixed(double value,unsigned digits){
 if(digits>2||!std::isfinite(value)||std::abs(value)>1e12)return {};
 uint64_t bits;std::memcpy(&bits,&value,sizeof(bits));bool negative=bits>>63;
 uint64_t significand=bits&((uint64_t(1)<<52)-1);
 int power=int((bits>>52)&2047);if(power)significand|=uint64_t(1)<<52;
 int exponent=power?power-1023-52:-1074;
 constexpr uint64_t fives[]={1,5,25},scales[]={1,10,100};
 significand*=fives[digits];exponent+=int(digits);uint64_t magnitude=0;
 if(exponent>=0)magnitude=significand<<exponent;
 else if(-exponent<64){
  unsigned shift=unsigned(-exponent);magnitude=significand>>shift;
  uint64_t remainder=significand&((uint64_t(1)<<shift)-1);
  if(remainder>=(uint64_t(1)<<(shift-1)))++magnitude;
 }
 auto result=std::string(negative?"-":"")+std::to_string(magnitude/scales[digits]);
 if(digits){auto fraction=std::to_string(magnitude%scales[digits]);result+="."+std::string(digits-fraction.size(),'0')+fraction;}
 return result;
}
}
