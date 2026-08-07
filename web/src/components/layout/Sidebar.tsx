import { NavLink, useNavigate } from 'react-router-dom';
import { FileText, History, LayoutDashboard, LogOut, Plus, Sparkles } from 'lucide-react';
import { useAuthStore } from '../../store';
import { cn } from '../../lib/utils';
const items=[['/dashboard/check','Новая проверка',Plus],['/dashboard/history','История',History],['/dashboard','Обзор',LayoutDashboard],['/dashboard/big-text','Большая проверка',Sparkles],['/dashboard/api','Настройки API',FileText]] as const;
export function Sidebar(){const {logout}=useAuthStore();const navigate=useNavigate();const out=async()=>{await logout();navigate('/')};return <aside className="lg:sticky lg:top-[96px] h-fit bg-white border border-black/[.08] rounded-2xl p-2 shadow-[0_1px_2px_rgba(0,0,0,.03)] overflow-x-auto">
  <p className="eyebrow px-3 pt-3 pb-2 hidden lg:block">Рабочая область</p><nav className="flex lg:flex-col gap-1 min-w-max lg:min-w-0">{items.map(([to,label,Icon])=><NavLink key={to} to={to} end={to==='/dashboard'} className={({isActive})=>cn('flex items-center gap-2.5 px-3 py-2.5 rounded-[10px] text-sm text-mv-text-secondary hover:text-black hover:bg-black/[.035]',isActive&&'bg-black text-white hover:bg-black hover:text-white')}><Icon size={16}/><span>{label}</span></NavLink>)}<button onClick={out} className="flex items-center gap-2.5 px-3 py-2.5 rounded-[10px] text-sm text-mv-text-muted hover:text-mv-fake lg:mt-4"><LogOut size={16}/>Выйти</button></nav>
  </aside>}
