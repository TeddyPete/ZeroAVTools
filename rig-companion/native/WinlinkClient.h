#pragma once
#include "WinlinkReceive.h"
#include "WinlinkSecurity.h"
#include "DartLower.h"
namespace winmail {
struct ClientConfig{
  enum class Transport{Tcp,Radio};Transport transport=Transport::Tcp;
  std::string callsign,password;bool useStationId=false;unsigned stationId=0;
  std::string login()const{return callsign+(useStationId && stationId?"-"+std::to_string(stationId):"");}
};
class Client{
public:
  enum class State{Idle,Connecting,Handshake,Exchange,Closing,Done,Failed};
  Receive exchange;
  State state=State::Idle;ClientConfig config;Md5 md5;
  std::function<void(const std::string&,char)> traffic;
  std::deque<std::string> notices;
  std::string error;
  bool sawData=false,canceled=false;
private:
  std::deque<std::string> output;
  std::string line,challenge;bool afterCr=false;
  void notice(const std::string& value){notices.push_back(value);if(traffic)traffic(value,0);}
  bool serverNotice(const std::string& text){
    auto value=dartcase::trim(text);if(value.rfind("***",0)!=0)return false;
    auto begin=value.find_first_not_of('*');value=begin==value.npos?"":dartcase::trim(value.substr(begin));
    if(!value.empty() && value[0]=='['){
      auto end=value.find(']');bool digits=end!=value.npos && end>1;
      for(size_t i=1;digits && i<end;++i)digits=value[i]>='0' && value[i]<='9';
      if(digits)value=dartcase::trim(value.substr(end+1));
    }
    auto lower=dartcase::lower(value);bool bad=false;
    for(auto word:{"error","fail","bad ","invalid","denied","reject","unauthorized","not authorized"})if(lower.find(word)!=lower.npos)bad=true;
    if(bad)abort(value.empty()?"Server rejected the Winlink session.":value);
    else if(!value.empty())notice(value);
    return true;
  }
  void startExchange(){
    std::string greeting="[RMS Express-1.7.28.0-B2FHM$]\r";
    if(!challenge.empty() && !config.password.empty()){
      std::string response;
      if(!secureLoginResponse(challenge,config.password,md5,response)){abort("Winlink authentication digest failed.");return;}
      greeting+=";PR: "+response+"\r";notice("Authenticating...");
    }
    output.push_back(std::move(greeting));exchange.beginClient();state=State::Exchange;
  }
  void handshake(std::string text){
    if(text.empty())return;
    if(traffic)traffic(text,'R');
    if(serverNotice(text))return;
    const auto trimmed=dartcase::trim(text),lower=dartcase::lower(trimmed);
    if(config.transport==ClientConfig::Transport::Tcp && lower=="callsign :"){
      output.push_back(config.login()+"\r");notice("Sent callsign: "+config.login());return;
    }
    if(config.transport==ClientConfig::Transport::Tcp && lower=="password :"){output.push_back("CMSTelnet\r");return;}
    if(!text.empty() && text.back()=='>'){startExchange();return;}
    auto space=text.find(' ');auto key=text.substr(0,space);
    for(auto& c:key)if(c>='a'&&c<='z')c-=32;
    if(key==";PQ:" && !config.password.empty()){challenge=space==text.npos?"":text.substr(space+1);return;}
    // Banner and SID records before the first '>' are informational.
  }
public:
  Client(){
    exchange.traffic=[this](const std::string& text,char role){if(traffic)traffic(text,role);};
    exchange.control=[this](const std::string& text){return serverNotice(text);};
  }
  Client(const Client&)=delete;Client& operator=(const Client&)=delete;
  bool active()const{return state==State::Connecting || state==State::Handshake || state==State::Exchange || state==State::Closing;}
  bool begin(ClientConfig next){
    if(active())return false;
    auto login=next.login();bool valid=!login.empty() && next.stationId<=15;
    for(unsigned char c:login)if(!((c>='A'&&c<='Z')||(c>='a'&&c<='z')||(c>='0'&&c<='9')||c=='-'))valid=false;
    if(!valid){error="Set a valid station callsign and station ID before Winlink.";state=State::Failed;return false;}
    exchange.reset();config=std::move(next);output.clear();notices.clear();error.clear();line.clear();challenge.clear();afterCr=false;sawData=canceled=false;state=State::Connecting;
    notice(config.transport==ClientConfig::Transport::Tcp?"Connecting to Winlink server...":"Connecting to Winlink radio station...");
    return true;
  }
  void connected(){if(state==State::Connecting){state=State::Handshake;notice("Connected. Waiting for Winlink login...");}}
  void abort(const std::string& why){error=why;exchange.abort(why);output.clear();exchange.output.clear();state=State::Failed;notice(why);}
  void cancel(){
    if(!active())return;canceled=true;output.clear();exchange.reset();line.clear();challenge.clear();state=State::Closing;notice("Disconnecting...");
  }
  void disconnected(){
    if(state==State::Closing || canceled){state=State::Done;notice(canceled?"Winlink canceled.":"Winlink session complete.");}
    else if(state!=State::Failed && state!=State::Done && state!=State::Idle)abort("Winlink connection ended before the exchange completed. Stored mail retained.");
  }
  void update(){
    exchange.resume();
    if(exchange.phase==Receive::Phase::Failed && state!=State::Failed){error=exchange.error;state=State::Failed;output.clear();exchange.output.clear();notice(error);}
    else if(state==State::Exchange && exchange.close)state=State::Closing;
  }
  void feed(const std::string& bytes){
    if(bytes.empty() || (state!=State::Handshake && state!=State::Exchange))return;
    sawData=true;size_t pos=0;
    while(pos<bytes.size() && state==State::Handshake){
      char c=bytes[pos++];
      if(afterCr){afterCr=false;if(c=='\n')continue;}
      if(c=='\r'||c=='\n'){afterCr=c=='\r';auto ready=std::move(line);line.clear();handshake(std::move(ready));}
      else if(line.size()<2048)line+=c;
      else abort("Winlink login line exceeded the protocol limit.");
    }
    // CMS prompts often have no newline and can be split at any socket boundary.
    if(state==State::Handshake && !line.empty()){
      auto lower=dartcase::lower(dartcase::trim(line));
      if(lower=="callsign :" || lower=="password :" || line.back()=='>'){auto ready=std::move(line);line.clear();handshake(std::move(ready));}
    }
    if(state==State::Exchange && pos<bytes.size())exchange.feed(bytes.substr(pos));
    update();
  }
  const std::string* control()const{
    if(state==State::Failed || state==State::Idle || state==State::Done)return nullptr;
    if(!output.empty())return &output.front();
    return exchange.output.empty()?nullptr:&exchange.output.front();
  }
  void controlSent(){
    auto value=control();if(!value)return;
    if(traffic)traffic(*value,'T');
    if(!output.empty())output.pop_front();else exchange.output.pop_front();
  }
  bool readyToClose()const{return state==State::Closing && !control();}
};
struct ClientRetry{
  unsigned attempts=0;
  bool canRetry(bool tcp,bool sawData,bool userCanceled)const{return tcp && !sawData && !userCanceled && attempts<3;}
  bool closed(bool tcp,bool sawData,bool userCanceled){if(!canRetry(tcp,sawData,userCanceled))return false;++attempts;return true;}
  static constexpr unsigned delayMs=2000;
};
}
