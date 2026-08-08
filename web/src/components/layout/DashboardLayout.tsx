import { Outlet, Link } from 'react-router-dom';
import { History, User } from 'lucide-react';
import { Sidebar } from './Sidebar';
export function DashboardLayout(){return <div className="min-h-screen bg-mv-bg pt-[86px]">
  <header className="fixed top-0 inset-x-0 z-50 pointer-events-none"><div className="container pt-4"><div className="glass-nav pointer-events-auto h-[64px] rounded-[18px] px-4 flex items-center justify-between"><Link to="/" className="flex items-center gap-2.5"><span className="w-12 h-12 rounded-[12px] bg-white/80 border border-black/[.12] shadow-[inset_0_1px_rgba(255,255,255,.9),0_2px_6px_rgba(0,0,0,.08)] flex items-center justify-center"><img src="/assets/img/yav-logo.png" alt="" className="w-10 h-10 object-contain drop-shadow-[0_1px_1px_rgba(0,0,0,.2)]"/></span><b>ЯВЬ</b></Link><div className="flex items-center gap-2"><Link to="/dashboard/history" className="btn-light !min-h-[40px] !px-4 flex items-center gap-2"><History size={15}/> <span className="hidden sm:inline">История</span></Link><Link to="/dashboard" className="btn-light !min-h-[40px] !px-4 flex items-center gap-2"><User size={15}/> <span className="hidden sm:inline">Профиль</span></Link></div></div></div></header>
  <div className="container pb-16 grid lg:grid-cols-[220px_1fr] gap-6 lg:gap-10"><Sidebar/><main className="min-w-0"><Outlet/></main></div>
  </div>}
