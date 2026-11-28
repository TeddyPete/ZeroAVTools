#pragma once
#include "WinlinkSend.h"
#include "WinlinkResponses.h"
#include <deque>
#include <sstream>
#include <functional>
namespace winmail {
struct Proposal {std::string mid;size_t raw=0,compressed=0;bool valid=false;};
class Receive {
public:
  enum class Phase {Text,Check,Data,Save,Prepare,Response,Send,Confirm,Commit,Failed};
  Phase phase=Phase::Text;
  uint32_t epoch=1;
  std::vector<Proposal> proposals;
  std::deque<Proposal> accepted;
  std::unique_ptr<Blocks> blocks;
  std::string error;
  std::deque<std::string> output,notices;
  bool close=false;
  std::function<void(const std::string&,char)> traffic;
  std::function<bool(const std::string&)> control;
  std::vector<Outgoing> outgoing;
  std::vector<Receipt> receipts;
  std::vector<std::string> attempted;
private:
  std::string line;
  Bytes backlog;
  unsigned sum=0;
  size_t budget=0,wireBytes=0;
  bool afterCr=false,needPrepare=false,lastExchange=true,moreOutgoing=false,clientRole=false;
  std::vector<std::string> dispositions;std::string afterCommit;
  size_t sendIndex=0,sendOffset=0;
  static constexpr size_t maxMail=1024*1024,maxBatch=4*1024*1024;
  void fail(const char* why){
    error=why;phase=Phase::Failed;close=true;blocks.reset();backlog.clear();outgoing.clear();receipts.clear();
  }
  void command(std::string text){
    if(text.empty() || phase==Phase::Failed)return;
    if(traffic)traffic(text,'R');
    if(control && control(text))return;
    auto space=text.find(' ');auto key=text.substr(0,space);
    std::string value=space==text.npos?"":text.substr(space+1);
    if(clientRole)for(auto& c:key)if(c>='a'&&c<='z')c-=32;
    if(key.empty()){fail("Empty Winlink command.");return;}
    if(key.front()=='['){
      if(text.size()<7 || text.find('-')==text.npos || text.compare(text.size()-2,2,"$]"))fail("Invalid Winlink session ID.");
      return;
    }
    if(key.front()==';')return;
    if(phase==Phase::Confirm && (key=="FF" || key=="FQ" || key=="FC")){
      afterCommit=std::move(text);phase=Phase::Commit;return;
    }
    if(key=="FS"){
      if(phase!=Phase::Response){fail("Unexpected outgoing proposal response.");return;}
      dispositions=proposalResponses(value,outgoing.size()+1);receipts.clear();bool sending=false;
      if(dispositions.size()!=outgoing.size()){fail("Outgoing proposal response count differs.");return;}
      for(size_t i=0;i<outgoing.size();++i){
        if(dispositions[i]=="Y" || dispositions[i]=="N")receipts.push_back(outgoing[i]);
        if(dispositions[i]=="Y")sending=true;
      }
      size_t count=0,held=0,deferred=0,total=0;
      for(size_t i=0;i<outgoing.size();++i){
        if(dispositions[i]=="Y"){++count;total+=outgoing[i].wire.size();}
        else if(dispositions[i]=="N")++held;else ++deferred;
      }
      notices.push_back("Peer accepted "+std::to_string(count)+", already held / declined "+std::to_string(held)+", deferred "+std::to_string(deferred)+". Sending "+std::to_string(total)+" bytes.");
      sendIndex=sendOffset=0;
      if(sending)phase=Phase::Send;
      else{moreOutgoing=true;phase=Phase::Commit;}
      return;
    }
    if(key=="FC"){
      if(phase!=Phase::Text){fail("Mail proposal arrived out of turn.");return;}
      if(proposals.size()>=5){fail("Too many mail proposals.");return;}
      for(unsigned char c:text)sum+=c;sum+=13;
      Proposal p;std::string type,raw,compressed,offset,extra;std::istringstream in(value);
      if((in>>type>>p.mid>>raw>>compressed>>offset) && !(in>>extra)){
        size_t resume=0;
        p.valid=type=="EM" && p.mid.size()==12 && validMid(p.mid) &&
          lengthValue(raw,p.raw) && lengthValue(compressed,p.compressed) &&
          lengthValue(offset,resume) && !resume && p.raw>0 && p.raw<=maxMail &&
          p.compressed>=8 && p.compressed<=maxMail*2;
      }
      if(p.valid){
        if(p.raw+p.compressed>maxBatch-budget)p.valid=false;
        else budget+=p.raw+p.compressed;
      }
      proposals.push_back(std::move(p));
    }else if(key=="F>"){
      if(phase!=Phase::Text){fail("Proposal terminator arrived out of turn.");return;}
      if(proposals.empty()){fail("Proposal list is empty.");return;}
      if(value.size()!=2){fail("Invalid proposal checksum.");return;}
      unsigned check=0;
      for(char c:value){
        unsigned n=c>='0'&&c<='9'?c-'0':c>='A'&&c<='F'?c-'A'+10:256;
        if(n>15){fail("Invalid proposal checksum.");return;}
        check=(check<<4)|n;
      }
      if(uint8_t(sum+check)){fail("Proposal checksum failed.");return;}
      phase=Phase::Check;
    }else if(key=="FF"){
      if(phase!=Phase::Text || !proposals.empty()){fail("Peer ended mail turn before completion.");return;}
      if(clientRole){output.push_back("FQ\r");close=true;}else{phase=Phase::Prepare;lastExchange=true;needPrepare=false;}
    }else if(key=="FQ"){
      if(phase!=Phase::Text || !proposals.empty()){fail("Peer closed mail turn before completion.");return;}
      close=true;
    }
    else{fail("Unexpected Winlink command.");}
  }
  void consume(const uint8_t* data,size_t length){
    size_t pos=0;
    while(pos<length && phase!=Phase::Failed && !close){
      if(phase==Phase::Check || phase==Phase::Save || phase==Phase::Prepare || phase==Phase::Commit){
        if(length-pos>maxBatch-backlog.size()){fail("Mail receive backlog exceeded.");return;}
        backlog.insert(backlog.end(),data+pos,data+length);return;
      }
      if(phase==Phase::Data){
        // The delimiter following a proposal response may be split CR/LF.
        if(afterCr){afterCr=false;if(data[pos]==10){++pos;continue;}}
        auto consumed=blocks->feed(data+pos,length-pos);pos+=consumed;wireBytes+=consumed;
        if(consumed && traffic)traffic("Receiving mail, "+std::to_string(wireBytes)+" bytes.",0);
        if(!blocks->error.empty()){fail(blocks->error.c_str());return;}
        if(blocks->complete()){
          if(wireBytes!=accepted.front().compressed){fail("Compressed mail length differs from proposal.");return;}
          phase=Phase::Save;
        }
        continue;
      }
      char c=char(data[pos++]);
      if(afterCr){afterCr=false;if(c==10)continue;}
      if(c==13 || c==10){afterCr=c==13;auto ready=std::move(line);line.clear();command(std::move(ready));}
      else if(line.size()<2048)line+=c;
      else{fail("Mail protocol line exceeded limit.");return;}
    }
  }
public:
  void reset(){
    auto next=epoch+1;auto observer=traffic;auto hook=control;*this=Receive{};epoch=next?next:1;traffic=std::move(observer);control=std::move(hook);
  }
  void abort(const std::string& why){fail(why.c_str());}
  void beginClient(){clientRole=true;lastExchange=false;phase=Phase::Prepare;}
  void feed(const std::string& data){consume(reinterpret_cast<const uint8_t*>(data.data()),data.size());}
  void checked(const std::vector<bool>& exists,bool success){
    if(phase!=Phase::Check)return;
    if(!success || exists.size()!=proposals.size()){fail("Mailbox could not verify message IDs.");return;}
    std::string response="FS ";
    for(size_t i=0;i<proposals.size();++i){
      auto& p=proposals[i];response+=!p.valid?'H':exists[i]?'N':'Y';
      if(p.valid && !exists[i])accepted.push_back(p);
    }
    output.push_back(response+"\r");proposals.clear();sum=budget=0;
    if(accepted.empty())phase=Phase::Text;
    else{blocks=std::make_unique<Blocks>(accepted.front().compressed);phase=Phase::Data;}
  }
  void saved(bool success){
    if(phase!=Phase::Save)return;
    if(!success){fail("Received mail was not saved and verified.");return;}
    accepted.pop_front();blocks.reset();wireBytes=0;
    if(accepted.empty()){phase=Phase::Text;if(clientRole)output.push_back("FF\r");else needPrepare=true;}
    else{blocks=std::make_unique<Blocks>(accepted.front().compressed);phase=Phase::Data;}
  }
  void prepared(std::vector<Outgoing> items,bool success,const std::string& why={}){
    if(phase!=Phase::Prepare)return;
    if(!success){fail(why.empty()?"Outbox could not be prepared.":why.c_str());return;}
    if(items.empty()){output.push_back(lastExchange?"FQ\r":"FF\r");close=lastExchange;phase=Phase::Text;return;}
    if(items.size()>5){fail("Too many outgoing proposals.");return;}
    outgoing=std::move(items);std::string text;unsigned check=0;
    for(auto& item:outgoing){
      if(item.mid.size()!=12 || !validMid(item.mid) || item.wire.empty()){fail("Invalid prepared outgoing mail.");return;}
      attempted.push_back(item.mid);
      auto fc="FC EM "+item.mid+" "+std::to_string(item.rawSize)+" "+std::to_string(item.wire.size())+" 0\r";
      for(unsigned char c:fc)check+=c;text+=fc;
    }
    static constexpr char hex[]="0123456789ABCDEF";uint8_t value=uint8_t(0-check);
    text+="F> ";text+=hex[value>>4];text+=hex[value&15];text+='\r';
    output.push_back(std::move(text));phase=Phase::Response;
  }
  std::string sendChunk(size_t limit){
    if(phase!=Phase::Send)return {};
    while(sendIndex<outgoing.size()){
      auto& wire=outgoing[sendIndex].wire;
      if(dispositions[sendIndex]!="Y" || sendOffset==wire.size()){++sendIndex;sendOffset=0;continue;}
      size_t count=std::min(limit,wire.size()-sendOffset);
      return {wire.begin()+sendOffset,wire.begin()+sendOffset+count};
    }
    return {};
  }
  void sentChunk(size_t length){
    if(phase==Phase::Send && sendIndex<outgoing.size() && length<=outgoing[sendIndex].wire.size()-sendOffset)sendOffset+=length;
  }
  void drained(){
    if(phase==Phase::Send && sendChunk(1).empty())phase=Phase::Confirm;
  }
  void committed(bool success,const std::string& why={}){
    if(phase!=Phase::Commit)return;
    if(!success){fail(why.empty()?"Sent state could not be saved. Outbox retained.":why.c_str());return;}
    outgoing.clear();receipts.clear();dispositions.clear();phase=Phase::Text;
    auto next=std::move(afterCommit);afterCommit.clear();
    if(moreOutgoing){moreOutgoing=false;if(clientRole)output.push_back("FF\r");else{lastExchange=true;phase=Phase::Prepare;}}
    else command(std::move(next));
  }
  void resume(){
    if(phase==Phase::Save || phase==Phase::Check || phase==Phase::Prepare || phase==Phase::Commit)return;
    if(!backlog.empty()){auto bytes=std::move(backlog);backlog.clear();consume(bytes.data(),bytes.size());}
    // A sender ends its turn with the last binary block. Upstream does not
    // append FF after sending mail; we must offer our own messages now.
    if(needPrepare && phase==Phase::Text && !close){needPrepare=false;lastExchange=false;phase=Phase::Prepare;}
  }
};
}
