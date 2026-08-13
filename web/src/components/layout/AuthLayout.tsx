import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

interface AuthLayoutProps { mode: 'login' | 'register'; children: ReactNode; }

export function AuthLayout({ mode, children }: AuthLayoutProps) {
  const isLogin = mode === 'login';
  return <main className="min-h-[100svh] bg-[#fafaf9] lg:h-[100svh] lg:overflow-hidden grid lg:grid-cols-2">
    <section className="hidden lg:flex h-[100svh] px-10 py-8 xl:px-14 xl:py-10 flex-col border-r border-black/[.08]">
      <Link to="/" className="flex items-center gap-3 w-fit"><span className="w-12 h-12 rounded-[13px] bg-white border border-black/[.12] shadow-[inset_0_1px_rgba(255,255,255,.9),0_4px_12px_rgba(0,0,0,.08)] flex items-center justify-center"><img src="/assets/img/yav-logo.png" alt="" className="w-10 h-10 object-contain"/></span><strong className="text-lg tracking-[-.025em]">ЯВЬ</strong></Link>
      <div className="my-auto max-w-[560px]"><p className="eyebrow !text-black mb-6">Рабочая система проверки</p><h1 className="text-[clamp(42px,4.2vw,64px)] leading-[1.02] tracking-[-.055em] font-semibold">{isLogin ? <>Возвращайтесь<br/>к доказательствам,<br/>а не к догадкам</> : <>Создайте пространство<br/>для проверяемых<br/>решений</>}</h1></div>
      <p className="text-sm xl:text-base text-mv-text-secondary leading-6 max-w-[520px]">{isLogin ? 'История проверок, подробные отчёты и настройки анализа в одном защищённом рабочем пространстве.' : 'Сохраняйте историю проверок, работайте с отчётами и возвращайтесь к результатам в любое время.'}</p>
    </section>
    <section className="min-h-[100svh] px-5 py-5 sm:px-8 lg:h-[100svh] flex items-center justify-center"><div className="w-full max-w-[440px]">{children}</div></section>
  </main>;
}
