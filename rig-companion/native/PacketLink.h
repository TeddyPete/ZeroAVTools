#pragma once
#include "benshi_proto.h"
#include <deque>
#include <map>
namespace packetlink {
using benshi::Bytes;
enum class State { Offline, Calling, Online, Closing };

// Wire decoding is negotiated per session; unnumbered frames always use one control byte.
struct WirePacket: benshi::Decoded {uint8_t ns=0,nr=0;bool poll=false;};
inline WirePacket decodeWire(const Bytes& raw,bool extended){
 WirePacket p;static_cast<benshi::Decoded&>(p)=benshi::decodeFrame(raw);if(!p.ok)return p;
 const size_t header=(2+p.path.size())*7;size_t offset=header+1;
 if((p.control&3)!=3){
  if(extended){if(offset>=raw.size()){p.ok=false;return p;}p.poll=raw[offset]&1;p.nr=raw[offset++]>>1;p.ns=p.control>>1;}
  else{p.poll=p.control&0x10;p.nr=p.control>>5;p.ns=(p.control>>1)&7;}
  if(!(p.control&1)){if(offset>=raw.size()){p.ok=false;return p;}p.pid=raw[offset++];}
  p.info.assign(raw.begin()+offset,raw.end());
 }else p.poll=p.control&0x10;
 return p;
}
struct Session {
  State state=State::Offline;
  std::string local,peer,received,notice;
  std::vector<std::string> receivedPackets;
  uint8_t vs=0,vr=0,va=0;
  int modulo=8;
  int mask()const{return modulo-1;}
  size_t window()const{return modulo==128?32:4;}
  std::map<uint8_t,std::pair<uint8_t,std::string>> receiveBuffer;
  struct Outstanding {uint8_t sequence;std::string text;};
  std::deque<Outstanding> outstanding;
  std::deque<uint8_t> resend;
  bool rejectException=false;
  uint32_t ackDelayMs=250,ackStarted=0,idleSince=0;
  bool ackPending=false,recovering=false;
  uint32_t adaptiveMs=0,srttMs=0;
  std::deque<std::string> applicationQueue;
  size_t queuedBytes=0;
  void resetLink(){const auto delay=ackDelayMs;*this={};ackDelayMs=delay;}
  uint32_t initialTimeoutMs()const{return 3000u*(2u*route.size()+1u);}
  void sampleRtt(uint32_t elapsed){
    if(!srttMs)srttMs=initialTimeoutMs()/2;
    srttMs=(7ull*srttMs+elapsed)/8;
    if(srttMs<1000)srttMs=1000+2000*route.size();
    adaptiveMs=2*srttMs;
    if(adaptiveMs<250||adaptiveMs>2*initialTimeoutMs()){adaptiveMs=initialTimeoutMs();srttMs=adaptiveMs/2;}
  }
  void backoff(){if(!srttMs)srttMs=initialTimeoutMs()/2;adaptiveMs=2*srttMs+250*retries;if(adaptiveMs>2*initialTimeoutMs())adaptiveMs=initialTimeoutMs();}
  bool queueData(const std::string& bytes){
    if(state!=State::Online||bytes.empty()||bytes.size()>65536-queuedBytes)return false;
    for(size_t offset=0;offset<bytes.size();offset+=256)applicationQueue.push_back(bytes.substr(offset,256));
    queuedBytes+=bytes.size();return true;
  }
  bool busy=false, inbound=false;
  Bytes pending;
  std::vector<benshi::Address> route;
  uint32_t timeoutMs()const{return adaptiveMs?adaptiveMs:initialTimeoutMs();}
  uint32_t sent=0;
  int retries=0;

  Bytes addresses(bool command)const{
    benshi::Address to,from;
    if(!benshi::parseCallsign(peer,to)||!benshi::parseCallsign(local,from))return {};
    Bytes out;benshi::encodeAddress(out,to,command,false);benshi::encodeAddress(out,from,!command,route.empty());
    for(size_t i=0;i<route.size();++i)benshi::encodeAddress(out,route[i],false,i+1==route.size());
    return out;
  }
  Bytes frame(uint8_t control,bool command,const std::string &text={})const{
    auto out=addresses(command);if(out.empty())return {};
    out.push_back(control);
    if(!(control&1)||(control&0xef)==0x03)out.push_back(0xf0);
    if(!(control&1)||(control&0xef)==0x03||(control&0xef)==0xe3)out.insert(out.end(),text.begin(),text.end());
    return out;
  }
  Bytes information(uint8_t ns,const std::string& text)const{
    if(modulo==8)return frame((vr<<5)|(ns<<1),true,text);
    auto out=addresses(true);out.push_back(ns<<1);out.push_back(vr<<1);out.push_back(0xf0);out.insert(out.end(),text.begin(),text.end());return out;
  }
  Bytes supervisory(uint8_t kind,bool command,bool poll,uint8_t nr){
    if(!command&&nr==vr)ackPending=false;
    if(modulo==8)return frame((nr<<5)|kind|(poll?0x10:0),command);
    auto out=addresses(command);out.push_back(kind);out.push_back((nr<<1)|(poll?1:0));return out;
  }
  Bytes fallback(uint32_t now){modulo=8;notice="Peer did not accept extended AX.25. Trying basic mode.";auto attempts=retries;auto f=track(frame(0x3f,true),now);retries=attempts;return f;}
  Bytes track(Bytes f,uint32_t now){
    pending=f;sent=now;retries=0;return f;
  }
  Bytes open(const std::string &from,const std::string &to,uint32_t now,const std::vector<benshi::Address>& path={},bool extended=false){
    if(path.size()>8)return {};
    for(const auto& hop:path){benshi::Address checked;if(!benshi::parseCallsign(hop.call+"-"+std::to_string(hop.ssid),checked))return {};}
    if(state!=State::Offline||from==to)return {};
    resetLink();local=from;peer=to;route=path;modulo=extended?128:8;
    auto f=frame(extended?0x7f:0x3f,true);if(f.empty())return {};
    state=State::Calling;return track(f,now);
  }
  void refreshPending(){pending=outstanding.empty()?Bytes{}:information(outstanding.front().sequence,outstanding.front().text);}
  bool goodAck(uint8_t nr)const{return ((nr-va)&mask())<=outstanding.size();}
  bool acknowledge(uint8_t nr,uint32_t now){
    if(!goodAck(nr))return false;
    const size_t count=(nr-va)&mask();
    for(size_t i=0;i<count;++i)outstanding.pop_front();
    va=nr;if(count){if(!recovering&&retries==0)sampleRtt(uint32_t(now-sent));sent=now;retries=0;}
    if(outstanding.empty())idleSince=now;refreshPending();return true;
  }
  Bytes sequenceRecovery(uint32_t now){
    outstanding.clear();resend.clear();receiveBuffer.clear();vs=vr=va=0;busy=false;rejectException=false;
    ackPending=false;recovering=false;applicationQueue.clear();queuedBytes=0;state=State::Calling;notice="AX.25 sequence error. Reconnecting; unacknowledged data was not confirmed.";
    return track(frame(modulo==128?0x7f:0x3f,true),now);
  }
  Bytes nextResend(uint32_t now){
    while(!resend.empty()){
      auto ns=resend.front();resend.pop_front();
      for(const auto& item:outstanding)if(item.sequence==ns){sent=now;ackPending=false;return information(ns,item.text);}
    }
    return {};
  }
  Bytes send(const std::string &text,uint32_t now){
    if(state!=State::Online||outstanding.size()>=window()||!resend.empty()||busy||text.empty()||text.size()>256)return {};
    auto f=information(vs,text);ackPending=false;
    if(outstanding.empty()){sent=now;retries=0;}
    outstanding.push_back({vs,text});vs=(vs+1)&mask();refreshPending();return f;
  }
  Bytes close(uint32_t now){
    if(state==State::Offline||state==State::Closing)return {};
    outstanding.clear();resend.clear();applicationQueue.clear();queuedBytes=0;ackPending=false;recovering=false;state=State::Closing;return track(frame(0x53,true),now);
  }
  Bytes retry(uint32_t now){
    if(state==State::Online){
      if(!busy){auto f=nextResend(now);if(!f.empty()){ackPending=false;return f;}}
      if(!busy&&!recovering&&!applicationQueue.empty()){
        auto f=send(applicationQueue.front(),now);if(!f.empty()){queuedBytes-=applicationQueue.front().size();applicationQueue.pop_front();return f;}
      }
      if(ackPending&&uint32_t(now-ackStarted)>=ackDelayMs){ackPending=false;return supervisory(1,false,false,vr);}
      if(pending.empty()&&!busy&&!recovering){
        if(uint32_t(now-idleSince)<300000)return {};
        recovering=true;retries=1;sent=now;ackPending=false;return supervisory(1,true,true,vr);
      }
    }
    if((pending.empty()&&!busy&&!recovering)||uint32_t(now-sent)<timeoutMs())return {};
    if(state==State::Calling&&modulo==128&&retries>=2)return fallback(now);
    if(++retries>10){auto final=state==State::Online?frame(0x0f,false):Bytes{};resetLink();notice="Peer timed out. Queued or unacknowledged data was not confirmed.";return final;}
    sent=now;backoff();
    if(state==State::Online){recovering=true;ackPending=false;return supervisory(1,true,true,vr);}
    return pending;
  }
  Bytes accept(const Bytes &raw,const std::string &myCall,bool listen,uint32_t now){
    received.clear();receivedPackets.clear();notice.clear();
    if(raw.size()<15)return {};
    const auto p=decodeWire(raw,modulo==128);
    if(!p.ok || p.dest!=myCall || p.src==myCall)return {};
    const bool cmd=(raw[6]&0x80)!=0;
    const uint8_t c=p.control, pf=p.poll?0x10:0, kind=c&0xef;
    if(state!=State::Offline && p.src!=peer)return {};
    // Reverse the received digipeater sequence and clear all H bits for replies.
    std::vector<benshi::Address> reversePath;
    for(auto i=p.path.rbegin();i!=p.path.rend();++i){auto name=*i;if(!name.empty()&&name.back()=='*')name.pop_back();benshi::Address hop;if(!benshi::parseCallsign(name,hop))return {};reversePath.push_back(hop);}
    if(state==State::Offline && kind==0x43 && cmd && listen){
      local=myCall;peer=p.src;route=reversePath;auto response=frame(0x63|pf,false);resetLink();return response;
    }
    if((kind==0x2f||kind==0x6f) && cmd && (listen||state!=State::Offline)){
      resetLink();state=State::Online;inbound=true;local=myCall;peer=p.src;route=reversePath;modulo=kind==0x6f?128:8;
      idleSince=now;notice="Connected to "+peer;return frame(0x63|pf,false);
    }
    if(state==State::Offline)return {};
    if(kind==0x43 && cmd){
      auto reply=frame(0x63|pf,false);resetLink();notice="Peer disconnected.";return reply;
    }
    if(state==State::Calling&&modulo==128&&!cmd&&(kind==0x0f||kind==0x87))return fallback(now);
    if(kind==0x0f && !cmd){resetLink();notice="Peer closed the link.";return {};}
    if(kind==0x63 && !cmd && pf){
      if(state==State::Calling){state=State::Online;pending.clear();idleSince=now;notice="Connected to "+peer;}
      else if(state==State::Closing){resetLink();notice="Disconnected.";}
      return {};
    }
    if(kind==0xe3&&cmd)return frame(0xe3|pf,false,p.info);
    if(kind==0xaf){
      if(cmd&&pf)return frame(0xbf,false);
      notice="AX.25 XID missing poll or unexpected response.";return {};
    }
    if(kind==0x87&&state==State::Online){modulo=8;auto reconnect=sequenceRecovery(now);notice="Peer reported FRMR. Reconnecting; unacknowledged data not confirmed.";return reconnect;}
    if(kind==0x03){
      received=p.info;receivedPackets.push_back(p.info);
      if(cmd&&pf)return state==State::Online?supervisory(1,false,true,vr):frame(0x1f,false);
      return {};
    }
    if(state!=State::Online || (c&3)==3)return {};
    const uint8_t nr=p.nr, supervisoryKind=c&15;
    // SREJ without F requests one sequence without cumulatively acknowledging it.
    if((c&1)&&supervisoryKind==13){
      busy=false;
      if(!goodAck(nr))return sequenceRecovery(now);
      if(pf&&!acknowledge(nr,now))return sequenceRecovery(now);
      resend.clear();resend.push_back(nr);
      for(unsigned char byte:p.info){uint8_t requested=modulo==128?(byte>>1):(byte>>5);
        bool exists=false;for(const auto& item:outstanding)if(item.sequence==requested)exists=true;
        if(exists&&std::find(resend.begin(),resend.end(),requested)==resend.end())resend.push_back(requested);
      }
      return nextResend(now);
    }
    if(!acknowledge(nr,now))return sequenceRecovery(now);
    if(recovering && !cmd && pf && (c&1)){
      recovering=false;retries=0;idleSince=now;
      if(supervisoryKind!=5){resend.clear();for(const auto& item:outstanding)resend.push_back(item.sequence);}
    }else if(recovering && outstanding.empty() && !(c&1)){recovering=false;retries=0;idleSince=now;}
    if(!(c&1)){
      if(p.info.size()>256)return sequenceRecovery(now);
      if(p.ns==vr){
        vr=(vr+1)&mask();rejectException=false;if(p.pid==0xf0){received=p.info;receivedPackets.push_back(p.info);}
        while(receiveBuffer.count(vr)){auto item=receiveBuffer[vr];receiveBuffer.erase(vr);if(item.first==0xf0){received+=item.second;receivedPackets.push_back(item.second);}vr=(vr+1)&mask();}
        if(modulo==128&&!receiveBuffer.empty())return supervisory(13,false,true,vr);
      }else if(modulo==128){
        if(((p.ns-vr)&mask())<63){receiveBuffer[p.ns]={p.pid,p.info};return supervisory(13,false,true,vr);}
        if(!p.poll)return {};
      }else if(!rejectException){rejectException=true;return supervisory(9,false,cmd&&pf,vr);}
      if(cmd&&pf){ackPending=false;return supervisory(1,false,true,vr);}
      if(!ackDelayMs)return supervisory(1,false,false,vr);
      if(!ackPending){ackPending=true;ackStarted=now;}return {};
    }
    if(supervisoryKind==5){busy=true;sent=now;}
    else if(supervisoryKind==1||supervisoryKind==9)busy=false;
    if(supervisoryKind==9){
      resend.clear();for(const auto& item:outstanding)resend.push_back(item.sequence);
      // A poll must be answered before the scheduled data retransmissions.
      if(cmd&&pf)return supervisory(1,false,true,vr);
      return nextResend(now);
    }
    if(cmd&&pf)return supervisory(1,false,true,vr);
    return {};
  }
};
}
