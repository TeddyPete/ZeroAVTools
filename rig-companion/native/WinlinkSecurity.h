#pragma once
#include "WinlinkBlocks.h"
#include "MailSort.h"
#include <array>
#include <functional>
namespace winmail {
using Digest=std::array<uint8_t,16>;
using Md5=std::function<bool(const Bytes&,Digest&)>;
inline Bytes loginBytes(const std::string& text){
  Bytes out;mailorder::Units units(text);unsigned unit=0;
  while(units.next(unit))out.push_back(uint8_t(unit));
  return out;
}
inline bool secureLoginResponse(const std::string& challenge,const std::string& password,const Md5& md5,std::string& response){
  // Literal WinlinkSecurity.secureLoginResponse from pinned upstream, including
  // its 0x3f mask (the upstream explanatory comment describes a different mask).
  static constexpr uint8_t salt[]={77,197,101,206,190,249,93,200,51,243,93,237,71,94,239,138,68,108,70,185,225,137,217,16,51,122,193,48,194,195,198,175,172,169,70,84,61,62,104,186,114,52,61,168,66,129,192,208,187,249,232,193,41,113,41,45,240,16,29,228,208,228,61,20};
  Bytes input=loginBytes(challenge),secret=loginBytes(password);input.insert(input.end(),secret.begin(),secret.end());input.insert(input.end(),std::begin(salt),std::end(salt));
  Digest digest{};if(!md5 || !md5(input,digest))return false;
  uint32_t value=uint32_t(digest[3]&0x3f)<<24|uint32_t(digest[2])<<16|uint32_t(digest[1])<<8|digest[0];
  char output[9];snprintf(output,sizeof(output),"%08lu",static_cast<unsigned long>(value%100000000));response=output;return true;
}
inline std::string loginChallenge(const std::array<uint8_t,8>& random){
  uint64_t value=0;for(unsigned i=0;i<8;++i)value|=uint64_t(random[i])<<(8*i);
  char output[9];snprintf(output,sizeof(output),"%08lu",static_cast<unsigned long>(value%100000000));return output;
}
}
