#pragma once
#include "WinlinkCompression.h"
#include <string>
namespace winmail {
inline uint8_t checksum(const Bytes& bytes){
  unsigned sum=0;for(auto b:bytes)sum+=b;return uint8_t(0-sum);
}
// Incremental B2F framing. Compression runs separately from fragment ingestion.
class Blocks {
  enum class State {Start,HeaderLength,Header,Command,Length,Data,Checksum,Done,Failed};
  State state=State::Start;
  size_t remaining=0,limit;
  Bytes header,payload;
  unsigned sum=0;
  void fail(const char* text){error=text;state=State::Failed;payload.clear();}
public:
  std::string subject,error;
  explicit Blocks(size_t compressedLimit):limit(compressedLimit){}
  bool complete()const{return state==State::Done;}
  size_t feed(const uint8_t* data,size_t length){
    size_t pos=0;
    while(pos<length && state!=State::Done && state!=State::Failed){
      auto b=data[pos++];
      switch(state){
        case State::Start:
          if(b!=1)fail("Missing B2F mail header.");else state=State::HeaderLength;
          break;
        case State::HeaderLength:
          remaining=b;if(remaining<3)fail("Invalid B2F header size.");else state=State::Header;
          break;
        case State::Header:
          header.push_back(b);
          if(!--remaining){
            size_t n=header.size();
            if(header[n-3] || header[n-2]!='0' || header[n-1])fail("Unsupported B2F resume offset.");
            else{subject.assign(header.begin(),header.end()-3);state=State::Command;}
          }
          break;
        case State::Command:
          if(b==2)state=State::Length;
          else if(b==4)state=State::Checksum;
          else fail("Invalid B2F block command.");
          break;
        case State::Length:
          remaining=b;
          if(!remaining || remaining>limit-payload.size())fail("B2F compressed size limit exceeded.");
          else state=State::Data;
          break;
        case State::Data:
          payload.push_back(b);sum+=b;if(!--remaining)state=State::Command;
          break;
        case State::Checksum:
          if(payload.size()<6 || uint8_t(sum+b))fail("B2F payload checksum failed.");
          else state=State::Done;
          break;
        default:break;
      }
    }
    return pos;
  }
  bool decode(Bytes& raw,size_t decodedLimit){
    raw.clear();
    if(!complete())return false;
    if(!Lzhuf::decode(payload,raw,decodedLimit)){fail("Winlink compressed mail CRC or length failed.");return false;}
    return true;
  }
};
inline bool encodeBlocks(const std::string& subject,const Bytes& raw,Bytes& wire){
  wire.clear();
  if(raw.empty() || subject.size()>252 || subject.find('\0')!=std::string::npos)return false;
  auto payload=Lzhuf::encode(raw);
  wire={1,uint8_t(subject.size()+3)};wire.insert(wire.end(),subject.begin(),subject.end());
  wire.insert(wire.end(),{0,'0',0});
  for(size_t pos=0;pos<payload.size();){
    size_t n=std::min(size_t(250),payload.size()-pos);wire.push_back(2);wire.push_back(uint8_t(n));
    wire.insert(wire.end(),payload.begin()+pos,payload.begin()+pos+n);pos+=n;
  }
  wire.push_back(4);wire.push_back(checksum(payload));return true;
}
}
