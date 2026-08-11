import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

interface AuthLayoutProps { mode: 'login' | 'register'; children: ReactNode; }

export function AuthLayout({ mode, children }: AuthLayoutProps) {
  const isLogin = mode === 'login';
  return <main className="min-h-screen bg-[#fafaf9] grid lg:grid-cols-2">
    <section className="min-h-[42vh] lg:min-h-screen p-7 sm:p-12 lg:p-16 xl:p-20 flex flex-col border-b lg:border-b-0 lg:border-r border-black/[.08]">
      <Link to="/" className="flex items-center gap-3 w-fit"><span className="w-16 h-16 rounded-[16px] bg-white border border-black/[.12] shadow-[inset_0_1px_rgba(255,255,255,.9),0_4px_12px_rgba(0,0,0,.09)] flex items-center justify-center"><img src="/assets/img/yav-logo.png" alt="" className="w-14 h-14 object-contain drop-shadow-[0_1px_1px_rgba(0,0,0,.2)]"/></span><strong className="text-xl tracking-[-.025em]">ЯВЬ</strong></Link>
      <div className="my-auto py-16 lg:py-8"><p className="eyebrow !text-black mb-10">Рабочая система проверки</p><h1 className="text-[48px] sm:text-[64px] xl:text-[72px] leading-[1.04] tracking-[-.055em] font-semibold max-w-[650px]">{isLogin ? <>Возвращайтесь<br/>к доказательствам,<br/>а не к догадкам</> : <>Создайте пространство<br/>для проверяемых<br/>решений</>}</h1></div>
      <p className="text-lg text-mv-text-secondary leading-8 max-w-[650px]">{isLogin ? 'История проверок, подробные отчёты и настройки анализа в одном защищённом рабочем пространстве.' : 'Сохраняйте историю проверок, работайте с отчётами и возвращайтесь к результатам в любое время.'}</p>
    </section>
    <section className="min-h-[58vh] lg:min-h-screen px-6 py-16 sm:p-12 lg:p-16 xl:p-20 flex items-center justify-center"><div className="w-full max-w-[550px]">{children}</div></section>
  </main>;
}
