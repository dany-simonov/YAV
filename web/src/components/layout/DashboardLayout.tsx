import { Outlet, Link } from 'react-router-dom';
import { History, User } from 'lucide-react';
import { Sidebar } from './Sidebar';
export function DashboardLayout(){return <div className="min-h-screen bg-mv-bg pt-[86px]">
  <header className="fixed top-0 inset-x-0 z-50 pointer-events-none"><div className="container pt-4"><div className="glass-nav pointer-events-auto h-[58px] rounded-[18px] px-4 flex items-center justify-between"><Link to="/" className="flex items-center gap-2.5"><span className="w-8 h-8 rounded-[9px] bg-black flex items-center justify-center"><img src="/assets/img/logo.png" alt="" className="w-6 h-6 brightness-0 invert"/></span><b>Источник</b></Link><div className="flex items-center gap-1"><Link to="/dashboard/history" className="px-3 py-2 text-sm text-mv-text-secondary hover:text-black flex items-center gap-2"><History size={16}/> <span className="hidden sm:inline">История</span></Link><Link to="/dashboard" className="px-3 py-2 text-sm text-mv-text-secondary hover:text-black flex items-center gap-2"><User size={16}/> <span className="hidden sm:inline">Профиль</span></Link></div></div></div></header>
  <div className="container pb-16 grid lg:grid-cols-[220px_1fr] gap-6 lg:gap-10"><Sidebar/><main className="min-w-0"><Outlet/></main></div>
  </div>}
