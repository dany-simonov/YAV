import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Menu, X } from 'lucide-react';

interface HeaderProps { onLoginClick: () => void; isLoggedIn: boolean; onLogout: () => void; }

const nav = [
  ['Возможности', '/#features'], ['Как это работает', '/#process'], ['Для кого', '/#audience'],
  ['Безопасность', '/#security'], ['Документация', '/docs'],
];

export function Header({ onLoginClick, isLoggedIn }: HeaderProps) {
  const [open, setOpen] = useState(false);
  return (
    <header className="fixed inset-x-0 top-0 z-50 pointer-events-none">
      <div className="container pt-4">
        <div className="glass-nav pointer-events-auto min-h-[58px] rounded-[18px] px-4 flex items-center justify-between gap-5">
          <Link to="/" className="flex items-center gap-2.5 shrink-0" aria-label="Источник — главная">
            <span className="w-8 h-8 rounded-[9px] bg-black flex items-center justify-center overflow-hidden"><img src="/assets/img/logo.png" alt="" className="w-6 h-6 brightness-0 invert" /></span>
            <span className="font-semibold tracking-[-.02em]">Источник</span>
          </Link>
          <nav className="hidden lg:flex items-center gap-7 text-[13px] text-mv-text-secondary">
            {nav.map(([label, to]) => <Link key={label} to={to} className="hover:text-black transition-colors">{label}</Link>)}
          </nav>
          <div className="hidden md:flex items-center gap-2 shrink-0">
            {isLoggedIn ? <Link to="/dashboard" className="btn-light !min-h-[40px] !px-4">Кабинет</Link> : <button onClick={onLoginClick} className="btn-light !min-h-[40px] !px-4">Войти</button>}
            <Link to={isLoggedIn ? '/dashboard/check' : '/register'} className="btn-black !min-h-[40px] !px-4">Проверить медиа</Link>
          </div>
          <button onClick={() => setOpen(!open)} className="md:hidden p-2 text-black" aria-label="Открыть меню" aria-expanded={open}>{open ? <X /> : <Menu />}</button>
        </div>
        {open && <nav className="glass-nav pointer-events-auto mt-2 rounded-2xl p-4 flex flex-col gap-1 md:hidden">
          {nav.map(([label, to]) => <Link key={label} to={to} onClick={() => setOpen(false)} className="px-3 py-2.5 text-sm rounded-lg hover:bg-black/5">{label}</Link>)}
          <div className="grid grid-cols-2 gap-2 mt-3 pt-3 border-t border-black/5">
            {isLoggedIn ? <Link to="/dashboard" className="btn-light">Кабинет</Link> : <button onClick={onLoginClick} className="btn-light">Войти</button>}
            <Link to={isLoggedIn ? '/dashboard/check' : '/register'} className="btn-black">Проверить</Link>
          </div>
        </nav>}
      </div>
    </header>
  );
}
