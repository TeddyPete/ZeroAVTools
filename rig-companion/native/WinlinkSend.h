#pragma once
#include "WinlinkDraft.h"
#include <set>
namespace winmail {
struct Receipt {
  std::string mid;
  uint64_t generation=0;
  size_t rawSize=0;
  uint32_t fingerprint=0;
};
struct Outgoing:Receipt {Bytes wire;};
inline uint32_t mailFingerprint(const Bytes& raw){
  return history::checksum(std::string(raw.begin(),raw.end()));
}
// Worker-only: compression and mailbox reads never run on the display loop.
template<class FS>bool prepareOutgoing(FS& fs,const std::vector<std::string>& mids,const std::string& station,
                                       const std::vector<std::string>& attempted,std::vector<Outgoing>& out,std::string& error,bool relay=false,bool* more=nullptr){
  out.clear();error.clear();if(more)*more=false;size_t budget=0;std::set<std::string> seen(attempted.begin(),attempted.end());
  for(size_t index=0;index<mids.size();++index){const auto& mid=mids[index];
    if(!seen.insert(mid).second)continue;
    if(fs.exists(mailPath(mid)+".del")){if(!recoverDeleted(fs,mid,error))return false;continue;}
    Managed mail;if(!loadManaged(fs,mid,mail,error)){error="Mail "+mid+": "+error;return false;}
    if(mail.box!='O' || mail.mail.message.mid.size()!=12 || (!relay && !addressedTo(mail.mail.message,station)))continue;
    auto& raw=mail.mail.raw;
    if(raw.size()>1024*1024 || mail.mail.message.subject.size()>252){
      error="Mail "+mid+" exceeds the B2F message or subject limit. Outbox retained.";return false;
    }
    Outgoing item;item.mid=mid;item.generation=mail.generation;item.rawSize=raw.size();item.fingerprint=mailFingerprint(raw);
    if(!encodeBlocks(mail.mail.message.subject,raw,item.wire)){error="Mail "+mid+" could not be compressed.";return false;}
    if(item.wire.size()>2*1024*1024){error="Compressed mail exceeds the B2F limit.";return false;}
    if(!out.empty() && budget+item.wire.size()>4*1024*1024){if(more)*more=true;break;}
    budget+=item.wire.size();out.push_back(std::move(item));fs.yieldIo();
    if(out.size()==5){if(more)*more=index+1<mids.size();break;}
  }
  return true;
}
template<class FS>bool commitOutgoing(FS& fs,const Receipt& receipt,std::string& error){
  Managed prior;if(!loadManaged(fs,receipt.mid,prior,error))return false;
  if(prior.mail.raw.size()!=receipt.rawSize || mailFingerprint(prior.mail.raw)!=receipt.fingerprint){
    error="Delivered mail content changed. Stored revision retained.";return false;
  }
  if(prior.box=='S' && prior.generation==receipt.generation+1)return true;
  if(prior.box!='O' || prior.generation!=receipt.generation){
    error="Delivered mail changed since it was proposed. Stored revision retained.";return false;
  }
  // Preserve original transport headers, body and attachments byte for byte.
  return publishManaged(fs,receipt.mid,prior.mail.raw,'S',receipt.generation,error);
}
}
