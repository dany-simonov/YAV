import { useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Menu, X } from 'lucide-react';

interface HeaderProps { isLoggedIn: boolean; onLogout: () => void; }

const nav = [
  { label: 'Возможности', section: 'features' },
  { label: 'Как это работает', section: 'process' },
  { label: 'Для кого', section: 'audience' },
  { label: 'Безопасность', section: 'security' },
  { label: 'Исследования', to: '/research' },
];

export function Header({ isLoggedIn }: HeaderProps) {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  const scrollToSection = (section: string) => {
    const scroll = () => {
      const target = document.getElementById(section);
      if (!target) return;
      const top = target.getBoundingClientRect().top + window.scrollY - 96;
      window.scrollTo({ top, behavior: 'smooth' });
    };

    setOpen(false);
    if (location.pathname === '/') scroll();
    else {
      navigate('/');
      window.setTimeout(scroll, 100);
    }
  };

  const handleLogoClick = (event: React.MouseEvent<HTMLAnchorElement>) => {
    setOpen(false);
    if (location.pathname === '/') {
      event.preventDefault();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  };

  return (
    <header className="fixed inset-x-0 top-0 z-50 pointer-events-none">
      <div className="container pt-4">
        <div className="glass-nav pointer-events-auto min-h-[58px] rounded-[18px] px-4 flex items-center justify-between gap-5">
          <Link to="/" onClick={handleLogoClick} className="flex items-center gap-2.5 shrink-0" aria-label="ЯВЬ — главная">
            <span className="w-12 h-12 rounded-[12px] bg-white/80 border border-black/[.12] shadow-[inset_0_1px_rgba(255,255,255,.9),0_2px_6px_rgba(0,0,0,.08)] flex items-center justify-center"><img src="/assets/img/yav-logo.png" alt="" className="w-10 h-10 object-contain drop-shadow-[0_1px_1px_rgba(0,0,0,.2)]" /></span>
            <span className="font-semibold tracking-[-.02em]">ЯВЬ</span>
          </Link>
          <nav className="hidden lg:flex items-center gap-7 text-[13px] text-mv-text-secondary">
            {nav.map((item) => item.section
              ? <button key={item.label} onClick={() => scrollToSection(item.section!)} className="hover:text-black transition-colors">{item.label}</button>
              : <Link key={item.label} to={item.to!} className="hover:text-black transition-colors">{item.label}</Link>)}
          </nav>
          <div className="hidden md:flex items-center gap-2 shrink-0">
            {isLoggedIn ? <Link to="/dashboard" className="btn-light !min-h-[40px] !px-4">Кабинет</Link> : <Link to="/login" className="btn-light !min-h-[40px] !px-4">Войти</Link>}
            <Link to={isLoggedIn ? '/dashboard/check' : '/register'} className="btn-black !min-h-[40px] !px-5 !bg-black !text-white hover:!bg-[#222]">Проверить медиа</Link>
          </div>
          <button onClick={() => setOpen(!open)} className="md:hidden p-2 text-black" aria-label="Открыть меню" aria-expanded={open}>{open ? <X /> : <Menu />}</button>
        </div>
        {open && <nav className="glass-nav pointer-events-auto mt-2 rounded-2xl p-4 flex flex-col gap-1 md:hidden">
          {nav.map((item) => item.section
            ? <button key={item.label} onClick={() => scrollToSection(item.section!)} className="px-3 py-2.5 text-sm text-left rounded-lg hover:bg-black/5">{item.label}</button>
            : <Link key={item.label} to={item.to!} onClick={() => setOpen(false)} className="px-3 py-2.5 text-sm rounded-lg hover:bg-black/5">{item.label}</Link>)}
          <div className="grid grid-cols-2 gap-2 mt-3 pt-3 border-t border-black/5">
            {isLoggedIn ? <Link to="/dashboard" className="btn-light">Кабинет</Link> : <Link to="/login" onClick={() => setOpen(false)} className="btn-light">Войти</Link>}
            <Link to={isLoggedIn ? '/dashboard/check' : '/register'} className="btn-black">Проверить</Link>
          </div>
        </nav>}
      </div>
    </header>
  );
}
