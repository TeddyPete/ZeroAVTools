#pragma once
#include "WinlinkClient.h"
namespace companion::rigwinlink {
// Storage/protocol worker adapter. The owner supplies the complete Outbox ID
// index and a checked filesystem, and pumps transport bytes separately. Never
// call this from a display/audio callback: decompression and IO can take time.
struct Session {
 winmail::Client client;
 std::vector<std::string> attempted;
 bool moreOutgoing=false;unsigned saved=0,sent=0;
 bool begin(winmail::ClientConfig config,winmail::Md5 md5,bool newSync=true){
  if(client.active())return false;
  if(newSync){attempted.clear();saved=sent=0;}
  moreOutgoing=false;client.md5=std::move(md5);client.traffic={};
  return client.begin(std::move(config));
 }
 template<class Files>bool storageStep(Files& fs,const std::vector<std::string>& outbox){
  using P=winmail::Receive::Phase;auto& e=client.exchange;std::string error;
  if(client.state!=winmail::Client::State::Exchange)return false;
  try{
   if(e.phase==P::Prepare){
    std::vector<winmail::Outgoing> items;bool more=false;
    bool ok=winmail::prepareOutgoing(fs,outbox,client.config.login(),attempted,items,error,true,&more);
    if(ok){for(const auto& item:items)attempted.push_back(item.mid);moreOutgoing|=more;}
    e.prepared(std::move(items),ok,error);
   }else if(e.phase==P::Check){
    std::vector<bool> exists;bool ok=true;
    for(const auto& p:e.proposals){
     auto path=winmail::mailPath(p.mid);
     if(p.valid&&!winmail::recoverDeleted(fs,p.mid,error)){ok=false;break;}
     bool present=p.valid&&(fs.exists(path)||fs.exists(path+".a")||fs.exists(path+".b"));exists.push_back(present);
     if(present){winmail::Managed previous;if(!winmail::loadManaged(fs,p.mid,previous,error)){ok=false;break;}}
     fs.yieldIo();
    }e.checked(exists,ok);
   }else if(e.phase==P::Save){
    bool ok=false;
    if(e.blocks&&!e.accepted.empty()){
     const auto p=e.accepted.front();winmail::Bytes raw;
     if(e.blocks->decode(raw,p.raw)&&raw.size()==p.raw){
      auto result=winmail::saveMail(fs,raw,p.mid,client.config.login(),1024*1024,error,true);
      ok=result==winmail::Saved::Stored||result==winmail::Saved::Duplicate;
     }
    }
    e.saved(ok);if(ok)++saved;
   }else if(e.phase==P::Commit){
    bool ok=true;unsigned count=0;
    for(const auto& receipt:e.receipts){if(!winmail::commitOutgoing(fs,receipt,error)){ok=false;break;}++count;fs.yieldIo();}
    e.committed(ok,error);sent+=count;
   }else return false;
   client.update();return true;
  }catch(const std::bad_alloc&){client.abort("Winlink memory low. Stored mail retained.");return true;}
  catch(const std::exception&){client.abort("Winlink storage failed. Stored mail retained.");return true;}
 }
};
}
