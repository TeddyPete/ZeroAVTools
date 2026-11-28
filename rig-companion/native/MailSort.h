#pragma once
#include <algorithm>
#include <cstdint>
#include <string>
#include <vector>
namespace mailorder {
// Dart String.compareTo orders UTF-16 code units, rather than UTF-8 bytes.
class Units {
  const std::string& text;size_t pos=0;unsigned pending=0;
public:
  explicit Units(const std::string& value):text(value){}
  size_t position()const{return pos;}
  bool next(unsigned& value){
    if(pending){value=pending;pending=0;return true;}
    if(pos==text.size())return false;
    unsigned first=uint8_t(text[pos++]),code=first;size_t count=0;
    if(first<128){value=first;return true;}
    if(first>=0xc2&&first<=0xdf){count=1;code=first&31;}
    else if(first>=0xe0&&first<=0xef){count=2;code=first&15;}
    else if(first>=0xf0&&first<=0xf4){count=3;code=first&7;}
    else{value=0xfffd;return true;}
    if(pos+count>text.size()){value=0xfffd;return true;}
    for(size_t i=0;i<count;++i){auto c=uint8_t(text[pos+i]);if((c&192)!=128){value=0xfffd;return true;}code=(code<<6)|(c&63);}
    if((count==1&&code<128)||(count==2&&code<2048)||(count==3&&code<65536)||code>0x10ffff||(code>=0xd800&&code<=0xdfff)){value=0xfffd;return true;}
    pos+=count;
    if(code>0xffff){code-=0x10000;pending=0xdc00+(code&1023);value=0xd800+(code>>10);}
    else value=code;
    return true;
  }
};
inline int compare(const std::string& a,const std::string& b){
  Units x(a),y(b);unsigned u=0,v=0;
  for(;;){bool px=x.next(u),py=y.next(v);if(!px||!py)return px?1:py?-1:0;if(u!=v)return u<v?-1:1;}
}
template<class Entry>void sort(std::vector<Entry>& rows,int column,bool ascending,bool recipient){
  std::stable_sort(rows.begin(),rows.end(),[&](const Entry& a,const Entry& b){
    const auto& x=column==0?(a.sortTime.empty()?a.date:a.sortTime):column==1?(recipient?a.to:a.from):a.subject;
    const auto& y=column==0?(b.sortTime.empty()?b.date:b.sortTime):column==1?(recipient?b.to:b.from):b.subject;
    auto order=compare(x,y);return ascending?order<0:order>0;
  });
}
}
