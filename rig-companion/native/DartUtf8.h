#pragma once
#include <algorithm>
#include <cstdint>
#include <string>
#include <string_view>
namespace dartutf8 {
// End a scheduled slice before an unfinished valid UTF8 prefix. Invalid
// standalone bytes can be split: each still produces its own replacement.
inline size_t sliceEnd(std::string_view text,size_t at,size_t budget){
 auto end=at+std::min(text.size()-at,std::max(size_t(4),budget));
 if(end==text.size()||end==at)return end;
 size_t lead=end-1;unsigned back=0;
 while(lead>at&&(uint8_t(text[lead])&192)==128&&back++<3)--lead;
 auto c=uint8_t(text[lead]);size_t need=c>=0xc2&&c<=0xdf?2:c>=0xe0&&c<=0xef?3:c>=0xf0&&c<=0xf4?4:0;
 return need&&end-lead<need?lead:end;
}
struct Part{size_t bytes=1;bool valid=false;};
inline Part part(std::string_view input,size_t at){
 const unsigned first=uint8_t(input[at]);
 size_t count=first<128?1:first>=0xc2&&first<=0xdf?2:first>=0xe0&&first<=0xef?3:first>=0xf0&&first<=0xf4?4:0;
 if(count>1&&at+1<input.size()){
  auto second=uint8_t(input[at+1]);
  if((first==0xe0&&second<0xa0)||(first==0xed&&second>=0xa0)||(first==0xf0&&second<0x90)||(first==0xf4&&second>=0x90))count=0;
 }
 size_t used=1;while(used<count&&at+used<input.size()&&(uint8_t(input[at+used])&192)==128)++used;
 return {used,count&&used==count};
}
struct Slice{std::string text;size_t end=0;};
inline Slice decodeSlice(std::string_view input,size_t at,size_t budget,bool stripBom){
 Slice result;budget=std::max(size_t(4),budget);result.text.reserve(std::min(budget,input.size()-at));
 if(stripBom&&at==0&&input.size()>=3&&input.substr(0,3)=="\xef\xbb\xbf")at=3;
 while(at<input.size()){
  auto value=part(input,at);auto length=value.valid?value.bytes:size_t(3);
  if(length>budget-result.text.size())break;
  if(value.valid)result.text.append(input.data()+at,value.bytes);else result.text+="\xef\xbf\xbd";
  at+=value.bytes;
 }
 result.end=at;return result;
}
// utf8.decode(bytes, allowMalformed:true): maximal subpart replacement and
// removal of one leading BOM. Each call is one complete conversion, as in
// the pinned Terminal/YAPP callbacks, rather than a stream spanning packets.
inline std::string decode(std::string_view input,bool stripBom=true){
 std::string output;output.reserve(input.size());
 size_t at=stripBom&&input.size()>=3&&input.substr(0,3)=="\xef\xbb\xbf"?3:0;
 while(at<input.size()){
  auto value=part(input,at);
  if(value.valid)output.append(input.data()+at,value.bytes);else output+="\xef\xbf\xbd";
  at+=value.bytes;
 }
 return output;
}
}
