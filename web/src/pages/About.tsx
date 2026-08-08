import { ArrowRight, Award, FileSearch, FlaskConical, Layers3, ShieldCheck } from 'lucide-react';
import { Link } from 'react-router-dom';

const CONTACT_EMAIL = 'yav.app@yandex.ru';

const directions = [
  { icon: FileSearch, title: 'Анализ цифрового контента', text: 'Исследуем признаки генерации и изменения изображений, аудио, видео и текста.' },
  { icon: Layers3, title: 'Объяснимый результат', text: 'Переводим вероятностные сигналы моделей в последовательный отчёт с ограничениями.' },
  { icon: ShieldCheck, title: 'Прикладные сценарии', text: 'Проектируем инструменты для работы с материалами, где важно понимать происхождение файла.' },
];

export function About() {
  return <div className="pt-32 pb-24">
    <div className="container">
      <section className="grid lg:grid-cols-[1.05fr_.95fr] gap-12 lg:gap-20 pb-20 border-b border-black/[.08]">
        <div><p className="eyebrow mb-7">О проекте ЯВЬ</p><h1 className="section-title max-w-3xl">Научно-прикладная платформа для проверки цифрового контента</h1></div>
        <div className="lg:pt-10"><p className="text-lg leading-8 text-mv-text-secondary">ЯВЬ объединяет исследование методов детекции синтетического контента и разработку рабочего инструмента, который помогает интерпретировать результаты анализа без ложного обещания абсолютной точности.</p></div>
      </section>

      <section className="py-20 grid lg:grid-cols-[.8fr_1.2fr] gap-12 lg:gap-20 items-start">
        <div><p className="eyebrow mb-6">Признание проекта</p><h2 className="text-3xl sm:text-4xl font-semibold tracking-[-.045em] leading-tight">Победитель конкурса предпринимательских проектов ФКН ВШЭ</h2></div>
        <article className="bg-[#0b0b0b] text-white rounded-[22px] p-8 sm:p-10 lg:p-12 shadow-[0_2px_3px_rgba(0,0,0,.08),0_28px_64px_rgba(0,0,0,.15)]"><Award size={28}/><p className="mt-12 text-2xl sm:text-3xl font-semibold tracking-[-.04em] leading-tight">Победа подтвердила актуальность задачи и потенциал ЯВЬ как технологического продукта.</p><p className="mt-6 text-white/55 leading-7">Проект развивается на пересечении машинного обучения, анализа медиаданных и практики принятия решений.</p></article>
      </section>

      <section className="py-20 border-y border-black/[.08]"><div className="grid md:grid-cols-2 gap-10 mb-14"><div><p className="eyebrow mb-6">Направление работы</p><h2 className="section-title">От исследовательской гипотезы к инструменту</h2></div><p className="text-mv-text-secondary leading-7 md:mt-auto max-w-xl md:ml-auto">Мы рассматриваем детекцию не как бинарный ответ «правда или ложь», а как систему вероятностных признаков, технических данных и контекста.</p></div>
        <div className="grid md:grid-cols-3 gap-5">{directions.map(item=><article key={item.title} className="bg-white border border-black/[.08] rounded-2xl p-7 min-h-[270px] flex flex-col"><item.icon size={23}/><div className="mt-auto"><h3 className="text-xl font-semibold tracking-[-.03em]">{item.title}</h3><p className="mt-4 text-sm leading-6 text-mv-text-secondary">{item.text}</p></div></article>)}</div>
      </section>

      <section className="py-20 grid lg:grid-cols-[.75fr_1.25fr] gap-12 lg:gap-20"><div><FlaskConical size={28}/><p className="eyebrow mt-8">Научно-прикладной подход</p></div><div className="space-y-8 text-lg leading-8 text-mv-text-secondary"><p>Исследовательская часть проекта посвящена оценке устойчивости методов детекции, сопоставлению сигналов и корректной интерпретации неопределённости.</p><p>Прикладная часть превращает эти методы в понятный пользовательский сценарий: загрузка материала, анализ, объяснение результата и сохранение контекста проверки.</p><p>Такой подход позволяет развивать продукт и одновременно проверять, насколько используемые методы полезны в реальных задачах.</p></div></section>

      <section className="bg-white border border-black/[.08] rounded-[22px] p-8 sm:p-12 flex flex-col md:flex-row md:items-center justify-between gap-8 shadow-[0_2px_3px_rgba(0,0,0,.04),0_20px_48px_rgba(0,0,0,.07)]"><div><h2 className="text-3xl font-semibold tracking-[-.04em]">Обсудить проект</h2><p className="mt-3 text-mv-text-secondary">Исследования, сотрудничество и применение платформы.</p></div><div className="flex flex-col sm:flex-row gap-3"><Link to="/research" className="btn-light">Исследования</Link><a href={`mailto:${CONTACT_EMAIL}`} className="btn-black">{CONTACT_EMAIL}<ArrowRight size={16}/></a></div></section>
    </div>
  </div>;
}
