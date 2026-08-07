import { Link } from 'react-router-dom';
const CONTACT_EMAIL = 'istochnik-media@yandex.com';
const columns = [
  { title:'Продукт', links:[['Возможности','/#features'],['Как это работает','/#process'],['Новая проверка','/dashboard/check']] },
  { title:'Ресурсы', links:[['Документация','/docs'],['Вопросы и ответы','/faq'],['История продукта','/history']] },
  { title:'Правовая информация', links:[['Конфиденциальность','/privacy'],['Условия использования','/terms'],['О проекте','/about']] },
];
export function Footer(){return <footer className="border-t border-black/[.07] pt-16 pb-8 bg-[#f7f7f6]">
  <div className="container"><div className="grid md:grid-cols-[1.5fr_2.5fr] gap-14 pb-16">
    <div><Link to="/" className="flex items-center gap-2.5 mb-5"><span className="w-8 h-8 rounded-[9px] bg-black flex items-center justify-center"><img src="/assets/img/logo.png" alt="" className="w-6 h-6 brightness-0 invert"/></span><b>Источник</b></Link><p className="text-sm text-mv-text-secondary max-w-[270px] leading-6">Проверка происхождения цифрового контента с понятными выводами и честными ограничениями.</p></div>
    <div className="grid grid-cols-2 sm:grid-cols-3 gap-8">{columns.map(c=><div key={c.title}><h4 className="text-xs font-semibold mb-4">{c.title}</h4><nav className="flex flex-col gap-3">{c.links.map(([l,t])=><Link key={l} to={t} className="text-sm text-mv-text-secondary hover:text-black">{l}</Link>)}</nav></div>)}</div>
  </div><div className="border-t border-black/[.06] pt-6 flex flex-col sm:flex-row justify-between gap-3 text-xs text-mv-text-muted"><span>© 2026 Источник. Все права защищены.</span><a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a></div></div>
  </footer>}
