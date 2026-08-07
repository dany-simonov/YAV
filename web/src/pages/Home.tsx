import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight, ChevronDown, FileUp, ScanSearch, ShieldCheck } from 'lucide-react';

const StatusDot=({tone='amber'}:{tone?:'amber'|'green'|'red'})=><span className={`w-2 h-2 rounded-full ${tone==='green'?'bg-mv-real':tone==='red'?'bg-mv-fake':'bg-mv-uncertain'}`}/>;

function ProductPreview(){return <div className="soft-card rounded-[22px] p-3 sm:p-5 relative">
  <div className="h-8 flex items-center gap-1.5 border-b border-black/[.06] mb-4"><i className="w-1.5 h-1.5 rounded-full bg-black/20"/><i className="w-1.5 h-1.5 rounded-full bg-black/10"/><span className="ml-auto text-[10px] text-mv-text-muted uppercase tracking-wider">Отчёт / 2026</span></div>
  <div className="grid sm:grid-cols-[1.08fr_.92fr] gap-4">
    <div className="min-h-[295px] rounded-[14px] bg-[#11151a] relative overflow-hidden p-5">
      <div className="absolute inset-0 opacity-20" style={{backgroundImage:'linear-gradient(rgba(90,150,220,.3) 1px,transparent 1px),linear-gradient(90deg,rgba(90,150,220,.3) 1px,transparent 1px)',backgroundSize:'36px 36px'}}/>
      <div className="absolute left-[18%] top-[20%] w-[52%] h-[48%] border border-blue-400/50"><span className="absolute -top-4 left-0 text-[8px] text-blue-300">AREA 01</span></div>
      <div className="absolute right-[12%] bottom-[16%] w-[22%] h-[24%] border border-blue-300/30"/>
      <div className="relative mt-auto h-full flex items-end"><span className="text-[10px] text-white/45 uppercase tracking-wider">Обнаружено 2 области</span></div>
    </div>
    <div className="p-2 sm:p-3 flex flex-col"><p className="eyebrow mb-4">Общий вывод</p><div className="flex items-center gap-2 text-sm text-mv-uncertain"><StatusDot/>Требуется проверка</div><div className="text-[52px] leading-none tracking-[-.06em] font-semibold mt-3 mb-7">76<span className="text-2xl text-mv-text-muted">%</span></div>
      <div className="space-y-4 text-xs"><div><div className="flex justify-between mb-2"><span>AI-генерация</span><span>24%</span></div><div className="h-1 bg-black/[.06] rounded-full"><div className="h-full w-[24%] bg-mv-uncertain rounded-full"/></div></div>
      <div className="flex justify-between border-t border-black/[.06] pt-3"><span className="text-mv-text-secondary">Метаданные</span><span className="flex items-center gap-2"><StatusDot tone="green"/>Сохранены</span></div><div className="flex justify-between border-t border-black/[.06] pt-3"><span className="text-mv-text-secondary">Редактирование</span><span className="flex items-center gap-2"><StatusDot/>Есть следы</span></div></div>
      <button className="btn-light !min-h-[40px] mt-auto">Открыть отчёт</button></div>
  </div></div>}

const process=[
  ['01','ЗАГРУЗКА','Загрузите материал','Изображение, аудио, текст или поддерживаемый файл для анализа.'],
  ['02','АНАЛИЗ','Система проверит признаки','Структуру файла, метаданные и вероятностные сигналы моделей.'],
  ['03','ОТЧЁТ','Получите понятный отчёт','Вывод, уровень уверенности, найденные признаки и ограничения.'],
];
const faqs=[['Какие форматы поддерживаются?','Изображения, аудио, видео и текст. Точный список форматов и лимиты показываются перед загрузкой.'],['Как долго выполняется проверка?','Обычно несколько секунд. Для больших видео и сложного анализа может потребоваться больше времени.'],['Сохраняются ли загруженные файлы?','Файл удаляется из временного хранилища после анализа. В истории сохраняется только результат проверки.'],['Может ли система ошибаться?','Да. Анализ вероятностный, поэтому отчёт показывает уверенность модели и ограничения результата.'],['Что означает уровень уверенности?','Это оценка надёжности вывода модели, а не абсолютное доказательство происхождения файла.'],['Можно ли использовать отчёт в работе?','Да, как вспомогательный материал для редакционной, образовательной и аналитической работы.']];

export function Home(){const [open,setOpen]=useState(0);return <div className="pt-[90px] overflow-hidden">
  <section className="border-b border-black/[.055]"><div className="container py-20 lg:py-28 grid lg:grid-cols-[.88fr_1.12fr] items-center gap-14 lg:gap-20">
    <div><p className="eyebrow mb-7">ПРОВЕРКА ПОДЛИННОСТИ ЦИФРОВОГО КОНТЕНТА</p><h1 className="display-title">Проверяйте<br/>подлинность<br/>медиа за<br/>секунды</h1><p className="mt-7 text-[17px] leading-7 text-mv-text-secondary max-w-[500px]">Загрузите изображение, видео, аудио или текст и получите понятный отчёт о возможных изменениях и признаках генерации искусственным интеллектом.</p><div className="flex flex-col sm:flex-row gap-3 mt-9"><a href="od://app/api/projects/14172975-5261-4c56-b5a4-6eed0548cf61/raw/upload.html" className="btn-black">Проверить медиа <ArrowRight size={16}/></a><a href="od://app/api/projects/14172975-5261-4c56-b5a4-6eed0548cf61/raw/report.html" className="btn-light">Посмотреть пример отчёта</a></div></div>
    <ProductPreview/>
  </div></section>

  <section id="audience" className="container py-14 lg:py-20 border-b border-black/[.07]">
    <div className="grid lg:grid-cols-[.9fr_1.1fr] gap-10 lg:gap-16 items-end">
      <div>
        <p className="eyebrow mb-5">Для профессиональных команд</p>
        <h2 className="text-[28px] sm:text-[34px] leading-[1.12] tracking-[-.04em] font-semibold max-w-[560px]">Создано для команд, которым важно подтверждать происхождение контента</h2>
      </div>
      <div className="grid sm:grid-cols-2 border-t border-black/[.1]">
        {['Журналистика','Фактчекинг','Образование','Информационная безопасность'].map((item,index)=><div key={item} className={`group flex items-center gap-4 py-5 border-b border-black/[.1] ${index%2===0?'sm:pr-6':'sm:pl-6 sm:border-l'}`}>
          <span className="text-[11px] tabular-nums text-mv-text-muted">0{index+1}</span>
          <span className="text-[17px] font-medium tracking-[-.02em] group-hover:translate-x-1 transition-transform">{item}</span>
        </div>)}
      </div>
    </div>
  </section>

  <section id="process" className="container section-space"><div className="grid md:grid-cols-2 gap-8 mb-14"><h2 className="section-title">От файла до<br/>понятного вывода</h2><p className="text-mv-text-secondary leading-7 max-w-md md:ml-auto">Система показывает результат по слоям: сначала общий вывод, затем доказательства, ограничения и технические детали.</p></div><div className="grid md:grid-cols-3 gap-5">{process.map((p,i)=><article key={p[0]} className="bg-white border border-black/[.08] rounded-2xl p-6 min-h-[350px] flex flex-col shadow-[0_1px_2px_rgba(0,0,0,.025)]"><div className="flex items-center gap-2 eyebrow"><span>{p[0]}</span><span className="text-black/20">/</span><span>{p[1]}</span></div><h3 className="font-semibold text-xl tracking-[-.025em] mt-7 mb-3">{p[2]}</h3><p className="text-sm leading-6 text-mv-text-secondary">{p[3]}</p>
    <div className="mt-auto pt-8">
      {i===0&&<div className="rounded-xl border border-black/[.08] bg-[#fafaf9] p-4"><div className="flex items-center gap-3"><span className="w-9 h-9 rounded-lg bg-white border border-black/[.08] flex items-center justify-center"><FileUp size={16}/></span><div className="min-w-0"><p className="text-sm font-medium truncate">photo_24.jpg <span className="font-normal text-mv-text-muted">· 4,8 МБ</span></p><p className="text-xs text-mv-real mt-1 flex items-center gap-1.5"><span className="w-1.5 h-1.5 bg-mv-real rounded-full"/>Файл готов к проверке</p></div></div></div>}
      {i===1&&<div className="rounded-xl border border-black/[.08] bg-[#fafaf9] p-4"><div className="flex items-center gap-3 mb-4"><ScanSearch size={17}/><span className="text-sm font-medium">Анализ структуры</span><span className="ml-auto w-4 h-4 rounded-full border-2 border-black/15 border-t-black animate-spin"/></div><div className="h-1.5 bg-black/[.06] rounded-full overflow-hidden"><div className="h-full w-[68%] bg-black rounded-full"/></div></div>}
      {i===2&&<div className="rounded-xl border border-black/[.08] bg-[#fafaf9] p-4"><div className="flex items-center justify-between gap-3"><span className="text-sm text-mv-uncertain flex items-center gap-2"><span className="w-1.5 h-1.5 bg-mv-uncertain rounded-full"/>Нужна проверка</span><ShieldCheck size={17}/></div><div className="border-t border-black/[.07] mt-4 pt-4 flex items-end justify-between"><span className="text-xs text-mv-text-muted">Уверенность модели</span><strong className="text-2xl tracking-[-.04em]">76%</strong></div></div>}
    </div></article>)}</div></section>

  <section id="features" className="container pb-20 lg:pb-32"><div className="grid md:grid-cols-2 gap-8 mb-14"><h2 className="section-title">Каждый вывод<br/>можно объяснить</h2><p className="text-mv-text-secondary leading-7 max-w-md md:ml-auto">Демонстрационные значения показывают структуру результата, а не фактический анализ конкретного файла.</p></div><div className="soft-card p-4 lg:p-6 grid lg:grid-cols-[1.1fr_.9fr] gap-7">
    <div className="min-h-[420px] rounded-[15px] bg-[#11151a] relative overflow-hidden grid-texture"><div className="absolute left-[20%] top-[18%] w-[48%] h-[58%] border border-blue-400/50"/><div className="absolute right-[12%] bottom-[14%] w-[18%] h-[28%] border border-blue-400/30"/></div>
    <div className="p-3 lg:p-8 flex flex-col"><p className="eyebrow mb-6">Пример анализа</p><h3 className="text-2xl lg:text-3xl font-semibold tracking-[-.035em] leading-tight">Есть признаки локального изменения</h3><p className="text-sm text-mv-text-secondary leading-6 mt-4 mb-8">Вывод вероятностный. Проверьте выделенные области и происхождение файла перед решением.</p>{[
      ['Вероятность AI-генерации','Низкая','green','Сильные признаки полной генерации не обнаружены.'],
      ['Целостность метаданных','Неполная','amber','Часть полей отсутствует после повторного сохранения.'],
      ['Области вмешательства','2 области','red','Локальные несоответствия структуры требуют ручной оценки.'],
    ].map(x=><div key={x[0]} className="py-4 border-t border-black/[.07] text-sm"><div className="flex justify-between gap-5"><span className="font-medium">{x[0]}</span><span className="flex items-center gap-2 shrink-0"><StatusDot tone={x[2] as any}/><strong>{x[1]}</strong></span></div><p className="mt-2 text-xs leading-5 text-mv-text-secondary pr-6">{x[3]}</p></div>)}<Link to="/dashboard/check" className="btn-black mt-5 self-start">Изучить результат</Link></div>
  </div></section>

  <section className="container pb-20 lg:pb-32"><h2 className="section-title max-w-2xl mb-14">Инструменты проверки<br/>в одной системе</h2><div className="grid md:grid-cols-12 gap-5">{[['Карта найденных признаков','Видимые области вмешательства и уровень каждого сигнала','md:col-span-7'],['Поля без загадок','Структурированные метаданные без технического шума','md:col-span-5'],['Подходящая шкала','Уверенность показана в контексте','md:col-span-4'],['Двойная проверка','Несколько моделей для сложных случаев','md:col-span-4'],['Контекст решений','Ограничения всегда рядом с выводом','md:col-span-4']].map((x,i)=><article key={x[0]} className={`${x[2]} bg-white border border-black/[.08] rounded-2xl p-7 min-h-[220px] flex flex-col`}><span className="eyebrow">0{i+1}</span><div className="my-auto space-y-2">{[70,42,85].map(n=><div key={n} className="h-1 bg-black/[.05] rounded"><div className="h-full bg-black/25 rounded" style={{width:n+'%'}}/></div>)}</div><h3 className="text-lg font-semibold mb-2">{x[0]}</h3><p className="text-sm text-mv-text-secondary leading-6">{x[1]}</p></article>)}</div></section>

  <section id="security" className="container pb-20 lg:pb-32"><div className="bg-[#0b0b0b] text-white rounded-[22px] p-8 md:p-12 lg:p-16 grid lg:grid-cols-[.9fr_1.1fr] gap-16"><div><p className="eyebrow !text-white/45 mb-8">Приватность и контроль</p><h2 className="section-title">Серьёзный<br/>инструмент не<br/>скрывает<br/>ограничения</h2><p className="mt-7 text-white/55 leading-7 max-w-sm">Мы показываем метод анализа, степень уверенности и границы применимости каждого результата.</p></div><div className="grid sm:grid-cols-2 gap-x-10">{[['Передача файлов','Шифрование'],['Хранение материалов','Временно'],['Понятные результаты','Всегда'],['Удаление истории','Под контролем'],['Точность','Вероятностная'],['Юридический статус','Вспомогательный']].map((x,i)=><div key={x[0]} className="py-6 border-t border-white/15"><p className="text-xs text-white/45 mb-3">{x[0]}</p><p className={`text-sm ${i>3?'text-[#d3a444]':'text-white'}`}>{x[1]}</p></div>)}</div></div></section>

  <section id="report" className="container section-space border-t border-black/[.06]"><div className="grid md:grid-cols-2 gap-8 mb-14"><h2 className="section-title">Отчёт, который<br/>читается без<br/>расшифровки</h2><p className="text-mv-text-secondary leading-7 max-w-md md:ml-auto">Общий вывод, доказательства, ограничения и технические детали собраны в спокойный документ.</p></div><ProductPreview/></section>

  <section className="container pb-20 lg:pb-32"><div className="grid lg:grid-cols-[.7fr_1.3fr] gap-12"><h2 className="section-title">Вопросы<br/>о проверке</h2><div>{faqs.map((q,i)=><div key={q[0]} className="border-t border-black/[.09]"><button className="w-full py-6 flex items-center justify-between text-left font-medium" onClick={()=>setOpen(open===i?-1:i)} aria-expanded={open===i}>{q[0]}<ChevronDown size={18} className={`transition-transform ${open===i?'rotate-180':''}`}/></button>{open===i&&<p className="text-sm text-mv-text-secondary leading-6 pb-6 pr-12">{q[1]}</p>}</div>)}</div></div></section>

  <section className="container pb-20 lg:pb-32"><div className="soft-card py-20 px-6 text-center"><h2 className="section-title">Проверьте материал до<br/>того, как ему поверят<br/>другие</h2><p className="text-mv-text-secondary mt-6 mb-9">Загрузите файл и получите понятный результат за несколько минут.</p><Link to="/dashboard/check" className="btn-black">Начать проверку <ArrowRight size={16}/></Link></div></section>
  </div>}
