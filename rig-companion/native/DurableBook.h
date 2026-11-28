#pragma once
#include <string>
#include <cstdint>
#include <limits>
namespace durablebook {
inline uint32_t checksum(const std::string& s){uint32_t n=2166136261u;for(unsigned char c:s)n=(n^c)*16777619u;return n;}
inline bool number(const std::string& s,uint64_t& n){n=0;if(s.empty())return false;for(char c:s){if(c<'0'||c>'9'||n>(UINT64_MAX-(c-'0'))/10)return false;n=n*10+c-'0';}return true;}
struct Snapshot{bool valid=false;uint64_t generation=0;std::string body;};
inline std::string encode(uint64_t generation,const std::string& body){auto data=std::to_string(generation)+"\n"+body;return "HTCB1 "+std::to_string(checksum(data))+"\n"+data;}
inline Snapshot decode(const std::string& data){
 Snapshot r;if(data.rfind("HTCB1 ",0)!=0)return r;auto e=data.find('\n'),g=data.find('\n',e+1);if(e==data.npos||g==data.npos)return r;
 uint64_t check;if(!number(data.substr(6,e-6),check)||check!=checksum(data.substr(e+1))||!number(data.substr(e+1,g-e-1),r.generation))return r;
 r.body=data.substr(g+1);r.valid=true;return r;
}
inline constexpr const char* paths[]={"/htcommander/contacts-a.dat","/htcommander/contacts-b.dat"};
template<class FS>Snapshot load(FS& fs,int& slot){auto a=decode(fs.read(paths[0])),b=decode(fs.read(paths[1]));slot=b.valid&&(!a.valid||b.generation>a.generation)?1:0;return slot?b:a;}
template<class FS>bool save(FS& fs,const std::string& body){int slot;auto previous=load(fs,slot);if(previous.generation==UINT64_MAX)return false;auto data=encode(previous.generation+1,body);auto target=paths[previous.valid?1-slot:0];return fs.write(target,data)&&fs.read(target)==data;}
inline bool userPath(const std::string& path){return path.size()>1&&path.size()<128&&path[0]=='/'&&path.find("..") ==path.npos&&path.find_first_of("\r\n\\")==path.npos&&path.rfind("/htcommander/",0)!=0&&path.size()>=4&&path.substr(path.size()-4)==".txt";}
}
