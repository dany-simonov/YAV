import { NavLink } from 'react-router-dom';
import { cn } from '../../lib/utils';

const items=[
  ['/dashboard/check','Новая проверка',true],
  ['/dashboard/history','История',false],
  ['/dashboard/check?example=report','Пример отчёта',false],
  ['/dashboard/api','Система компонентов',false],
] as const;

export function Sidebar(){return <aside className="lg:sticky lg:top-[112px] h-fit bg-white border border-black/[.075] rounded-[16px] p-2.5 shadow-[0_2px_3px_rgba(0,0,0,.04),0_18px_40px_rgba(0,0,0,.07)] overflow-x-auto">
  <nav className="flex lg:flex-col gap-1 min-w-max lg:min-w-0">{items.map(([to,label,end])=><NavLink key={to} to={to} end={end} className={({isActive})=>cn('px-4 py-3 rounded-[10px] text-sm text-mv-text-secondary hover:text-black hover:bg-black/[.025] transition-colors',isActive&&'bg-[#fafaf9] text-black font-semibold shadow-[0_1px_3px_rgba(0,0,0,.04)]')}>{label}</NavLink>)}</nav>
  </aside>}
