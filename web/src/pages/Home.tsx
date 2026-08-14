import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight, ChevronDown, FileUp, ScanSearch, ShieldCheck } from 'lucide-react';

const StatusDot=({tone='amber'}:{tone?:'amber'|'green'|'red'})=><span className={`w-2 h-2 rounded-full ${tone==='green'?'bg-mv-real':tone==='red'?'bg-mv-fake':'bg-mv-uncertain'}`}/>;

function ProductPreview(){return <div className="soft-card rounded-[22px] p-3 sm:p-5 relative">
  <div className="h-8 flex items-center gap-1.5 border-b border-black/[.06] mb-4"><i className="w-1.5 h-1.5 rounded-full bg-black/20"/><i className="w-1.5 h-1.5 rounded-full bg-black/10"/><span className="ml-auto text-[10px] text-mv-text-muted uppercase tracking-wider">Отчёт / 2026</span></div>
  <div className="grid sm:grid-cols-[1.08fr_.92fr] gap-4">
    
    <div className="min-h-[295px] rounded-[14px] bg-[#11151a] relative overflow-hidden p-5">
      <div className="absolute inset-0 opacity-20" style={{backgroundImage:'linear-gradient(rgba(90,150,220,.3) 1px,transparent 1px),linear-gradient(90deg,rgba(90,150,220,.3) 1px,transparent 1px)',backgroundSize:'36px 36px'}}/>
      <img
        src="/assets/img/puhosos.png"
        alt="Пример обнаруженных изменений"
        className="absolute inset-0 w-full h-full object-cover"
        />
      

      <div className="absolute left-[18%] top-[20%] w-[52%] h-[48%] border border-blue-400/50"><span className="absolute -top-4 left-0 text-[8px] text-blue-300">AREA 01</span></div>
      <div className="absolute right-[12%] bottom-[16%] w-[22%] h-[24%] border border-blue-300/30"/>

    </div>
    <div className="p-2 sm:p-3 flex flex-col"><p className="eyebrow mb-4">Общий вывод</p><div className="flex items-center gap-2 text-sm text-mv-fake"><StatusDot tone="red"/>Высокая вероятность AI-генерации</div><div className="text-[52px] leading-none tracking-[-.06em] font-semibold mt-3">94<span className="text-2xl text-mv-text-muted">%</span></div><p className="mt-2 mb-7 text-xs text-mv-text-secondary">вероятность AI-генерации</p>
      <div className="space-y-4 text-xs"><div><div className="flex justify-between mb-2"><span>AI-генерация</span><strong>94%</strong></div><div className="h-1 bg-black/[.06] rounded-full"><div className="h-full w-[94%] bg-mv-fake rounded-full"/></div></div>
      <div className="flex justify-between border-t border-black/[.06] pt-3"><span className="text-mv-text-secondary">Уверенность модели</span><strong>97%</strong></div><div className="flex justify-between border-t border-black/[.06] pt-3"><span className="text-mv-text-secondary">Метаданные</span><span className="flex items-center gap-2"><StatusDot tone="green"/>Сохранены</span></div><div className="flex justify-between border-t border-black/[.06] pt-3"><span className="text-mv-text-secondary">Редактирование</span><span className="flex items-center gap-2"><StatusDot/>Есть следы</span></div></div>
      </div>
  </div></div>}

const process=[
  ['01','ЗАГРУЗКА','Загрузите материал','Изображение, аудио, текст или поддерживаемый файл для анализа.'],
  ['02','АНАЛИЗ','Система проверит признаки','Структуру файла, метаданные и вероятностные сигналы моделей.'],
  ['03','ОТЧЁТ','Получите понятный отчёт','Вывод, уровень уверенности, найденные признаки и ограничения.'],
];
const faqs=[['Какие форматы поддерживаются?','Изображения, аудио, видео и текст. Точный список форматов и лимиты показываются перед загрузкой.'],['Как долго выполняется проверка?','Обычно несколько секунд. Для больших видео и сложного анализа может потребоваться больше времени.'],['Сохраняются ли загруженные файлы?','Файл удаляется из временного хранилища после анализа. В истории сохраняется только результат проверки.'],['Может ли система ошибаться?','Да. Анализ вероятностный, поэтому отчёт показывает уверенность модели и ограничения результата.'],['Что означает уровень уверенности?','Это оценка надёжности вывода модели, а не абсолютное доказательство происхождения файла.'],['Можно ли использовать отчёт в работе?','Да, как вспомогательный материал для редакционной, образовательной и аналитической работы.']];

export function Home(){
  const [open,setOpen]=useState(0);
  const scrollToReport=()=>{
    const report=document.getElementById('report');
    if(!report)return;
    const top=report.getBoundingClientRect().top+window.scrollY-96;
    window.scrollTo({top,behavior:'smooth'});
  };

  return <div className="pt-[90px] overflow-hidden">
  <section className="border-b border-black/[.055]"><div className="container py-20 lg:py-28 grid lg:grid-cols-[.88fr_1.12fr] items-center gap-14 lg:gap-20">
    <div><p className="eyebrow mb-7">ПРОВЕРКА ПОДЛИННОСТИ ЦИФРОВОГО КОНТЕНТА</p><h1 className="display-title">Проверяйте<br/>подлинность<br/>медиа за<br/>секунды</h1><p className="mt-7 text-[17px] leading-7 text-mv-text-secondary max-w-[500px]">Загрузите изображение, видео, аудио или текст и получите понятный отчёт о возможных изменениях и признаках генерации искусственным интеллектом.</p><div className="flex flex-col sm:flex-row gap-3 mt-9"><Link to="/dashboard/check" className="btn-black">Проверить медиа <ArrowRight size={16}/></Link><button type="button" onClick={scrollToReport} aria-controls="report" className="btn-light">Посмотреть пример отчёта</button></div></div>
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
	    <div className="aspect-[3/2] self-center rounded-[15px] bg-[#11151a] relative overflow-hidden grid-texture">
      <img
      src="/assets/img/iipicture.png"
      alt="Пример анализа"
      className="absolute inset-0 w-full h-full object-contain rounded-[12px]">
      </img>
      <div className="absolute left-[20%] top-[18%] w-[48%] h-[58%] border border-blue-400/50"/><div className="absolute right-[12%] bottom-[14%] w-[18%] h-[28%] border border-blue-400/30"/></div>
    <div className="p-3 lg:p-8 flex flex-col"><p className="eyebrow mb-6">Пример анализа</p><h3 className="text-2xl lg:text-3xl font-semibold tracking-[-.035em] leading-tight">Есть признаки локального изменения</h3><p className="text-sm text-mv-text-secondary leading-6 mt-4 mb-8">Вывод вероятностный. Проверьте выделенные области и происхождение файла перед решением.</p>{[
      ['Вероятность AI-генерации','Низкая','green','Сильные признаки полной генерации не обнаружены.'],
      ['Целостность метаданных','Неполная','amber','Часть полей отсутствует после повторного сохранения.'],
      ['Области вмешательства','2 области','red','Локальные несоответствия структуры требуют ручной оценки.'],
    ].map(x=><div key={x[0]} className="py-4 border-t border-black/[.07] text-sm"><div className="flex justify-between gap-5"><span className="font-medium">{x[0]}</span><span className="flex items-center gap-2 shrink-0"><StatusDot tone={x[2] as any}/><strong>{x[1]}</strong></span></div><p className="mt-2 text-xs leading-5 text-mv-text-secondary pr-6">{x[3]}</p></div>)}<Link to="/dashboard/check" className="btn-black mt-5 self-start">Изучить результат</Link></div>
  </div></section>
  <section className="relative pb-20 lg:pb-32">
    <div className="absolute inset-x-0 top-0 bottom-20 lg:bottom-32 pointer-events-none opacity-50" style={{backgroundImage:'linear-gradient(rgba(0,0,0,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(0,0,0,.025) 1px,transparent 1px)',backgroundSize:'44px 44px'}}/>
    <div className="container relative py-8 lg:py-14">
      <div className="grid md:grid-cols-[.82fr_1.18fr] items-end gap-8 lg:gap-16 mb-14">
        <h2 className="section-title max-w-xl">Инструменты<br/>проверки в одной<br/>системе</h2>
        <p className="text-mv-text-secondary leading-7 max-w-xl md:ml-auto">Сценарии основаны на текущем продукте: проверка медиа и текста, история, подробные результаты и API-настройки.</p>
      </div>

      <div className="grid md:grid-cols-12 gap-5">
        <article className="md:col-span-7 bg-white border border-black/[.075] rounded-[20px] p-7 lg:p-9 min-h-[330px] flex flex-col shadow-[0_2px_3px_rgba(0,0,0,.04),0_18px_44px_rgba(0,0,0,.07)]">
          <p className="eyebrow">Изображения</p>
          <h3 className="text-2xl font-semibold tracking-[-.035em] mt-8">Карта найденных признаков</h3>
          <p className="mt-3 text-mv-text-secondary leading-6">Переходите от общего вывода к конкретным областям изображения.</p>
          <div className="mt-auto pt-10">
            <div className="h-[118px] flex items-end gap-2 sm:gap-3" aria-label="Диаграмма найденных признаков">
              {[36,58,47,94,66,44].map((height,index)=><div key={index} className="group flex-1 h-full flex items-end relative"><div className={`w-full rounded-t-[4px] transition-all duration-200 group-hover:bg-black ${index===3?'bg-black/60':'bg-black/35'}`} style={{height:`${height}%`}}/><span className="absolute -bottom-5 left-1/2 -translate-x-1/2 text-[9px] text-mv-text-muted opacity-0 group-hover:opacity-100">0{index+1}</span></div>)}
            </div>
            <div className="h-px bg-black/15 mt-px"/>
          </div>
        </article>

        <article className="md:col-span-5 bg-white border border-black/[.075] rounded-[20px] p-7 lg:p-9 min-h-[330px] flex flex-col shadow-[0_2px_3px_rgba(0,0,0,.04),0_18px_44px_rgba(0,0,0,.07)]">
          <p className="eyebrow">Метаданные</p>
          <h3 className="text-2xl font-semibold tracking-[-.035em] mt-8">Поля без догадок</h3>
          <div className="mt-auto pt-8 text-sm">{[['Формат','JPEG'],['Размер','4,8 МБ'],['EXIF','Частично'],['Дата','Не подтверждена']].map(row=><div key={row[0]} className="flex justify-between gap-4 py-2.5 border-b border-black/[.07]"><span className="text-mv-text-secondary">{row[0]}</span><strong className="font-medium text-right">{row[1]}</strong></div>)}</div>
        </article>

        <article className="md:col-span-4 bg-white border border-black/[.075] rounded-[20px] p-7 lg:p-9 min-h-[300px] flex flex-col shadow-[0_2px_3px_rgba(0,0,0,.04),0_18px_44px_rgba(0,0,0,.07)]">
          <p className="eyebrow">Видео</p><h3 className="text-2xl font-semibold tracking-[-.035em] mt-8">Покадровая шкала</h3><p className="mt-3 text-mv-text-secondary leading-7">Возможность заявлена как «скоро» в текущем продукте.</p><div className="mt-auto pt-8"><div className="h-1.5 bg-black/[.07] rounded-full overflow-hidden"><div className="h-full w-[57%] bg-black rounded-full"/></div><div className="flex justify-between mt-3 text-[10px] text-mv-text-muted"><span>00:00</span><span>00:24</span></div></div>
        </article>

        <article className="md:col-span-4 bg-white border border-black/[.075] rounded-[20px] p-7 lg:p-9 min-h-[300px] flex flex-col shadow-[0_2px_3px_rgba(0,0,0,.04),0_18px_44px_rgba(0,0,0,.07)]">
          <p className="eyebrow">Текст</p><h3 className="text-2xl font-semibold tracking-[-.035em] mt-8">Двойная проверка</h3><p className="mt-3 text-mv-text-secondary leading-7">Детекция AI-признаков и фактчек с пословной подсветкой.</p><div className="mt-auto pt-8 flex gap-2"><span className="h-2 flex-[4] bg-black/15 rounded-sm"/><span className="h-2 flex-[2] bg-mv-uncertain/55 rounded-sm"/><span className="h-2 flex-[3] bg-black/30 rounded-sm"/><span className="h-2 flex-1 bg-mv-fake/50 rounded-sm"/></div>
        </article>

        <article className="md:col-span-4 bg-white border border-black/[.075] rounded-[20px] p-7 lg:p-9 min-h-[300px] flex flex-col shadow-[0_2px_3px_rgba(0,0,0,.04),0_18px_44px_rgba(0,0,0,.07)]">
          <p className="eyebrow">История</p><h3 className="text-2xl font-semibold tracking-[-.035em] mt-8">Контекст решений</h3><p className="mt-3 text-mv-text-secondary leading-7">Поиск, фильтры по формату и быстрый возврат к результату.</p><Link to="/dashboard/history" className="btn-light !min-h-[42px] mt-auto self-start">Открыть историю</Link>
        </article>
      </div>
    </div>
  </section>

  <section className="container pb-20 lg:pb-32">
    <div className="border-t border-black/[.08] pt-16 lg:pt-24">
      <p className="eyebrow mb-6">Для профессиональной работы</p>
      <h2 className="section-title max-w-3xl mb-14">Для решений, где<br/>важен источник</h2>
      <div className="grid md:grid-cols-2 border-t border-black/[.1]">
        {[
          ['СМИ и журналисты','Проверяйте пользовательские материалы перед публикацией и сохраняйте объяснимый результат.'],
          ['Информационная безопасность','Отделяйте первичный сигнал от технических деталей для дальнейшего расследования.'],
          ['Образовательные организации','Разбирайте признаки синтетического контента на понятных примерах, не выдавая оценку за гарантию.'],
          ['Компании и государственные команды','Проверяйте материалы для внешних коммуникаций по единому воспроизводимому сценарию.'],
        ].map((item,index)=><article key={item[0]} className={`py-9 lg:py-11 border-b border-black/[.1] ${index%2===0?'md:pr-10 lg:pr-16':'md:pl-10 lg:pl-16 md:border-l'}`}>
          <div className="flex items-start gap-5">
            <span className="text-[11px] tabular-nums text-mv-text-muted mt-1">0{index+1}</span>
            <div><h3 className="text-xl lg:text-2xl font-semibold tracking-[-.03em]">{item[0]}</h3><p className="mt-4 text-mv-text-secondary leading-7 max-w-lg">{item[1]}</p></div>
          </div>
        </article>)}
      </div>
    </div>
  </section>

  <section id="security" className="container pb-20 lg:pb-32"><div className="bg-[#0b0b0b] text-white rounded-[22px] p-8 md:p-12 lg:p-16 grid lg:grid-cols-[.9fr_1.1fr] gap-16"><div><p className="eyebrow !text-white/45 mb-8">Приватность и контроль</p><h2 className="section-title">Серьёзный<br/>инструмент не<br/>скрывает<br/>ограничения</h2><p className="mt-7 text-white/55 leading-7 max-w-md">Текущая политика сообщает, что медиафайлы не хранятся постоянно и удаляются после получения результата. Детали реализации требуют подтверждения команды.</p></div><div className="grid sm:grid-cols-2 gap-x-10">{[
    ['Передача файлов','HTTPS + защищённый доступ Appwrite',true],
    ['Понятные результаты','Вывод + объяснение',false],
    ['Хранение материалов','Только на время обработки',false],
    ['Удаление истории','Доступно пользователю в кабинете',true],
    ['Точность','Вероятностная оценка',false],
    ['Юридический статус','Не является экспертным заключением',false],
  ].map((x)=><div key={x[0] as string} className="py-6 border-t border-white/15"><p className="text-xs text-white/45 mb-3">{x[0]}</p><p className={`text-sm leading-5 ${x[2]?'text-[#d3a444]':'text-white'}`}>{x[1]}</p></div>)}</div></div></section>

  <section id="report" className="container section-space border-t border-black/[.06]">
    <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-8 mb-14">
      <h2 className="section-title">Отчёт, который<br/>читается без<br/>расшифровки</h2>
      <Link to="/dashboard/check" className="btn-light self-start md:self-auto">Проверить свой материал</Link>
    </div>
    <article className="bg-white border border-black/[.075] rounded-[20px] p-7 sm:p-9 lg:p-10 shadow-[0_2px_3px_rgba(0,0,0,.045),0_22px_54px_rgba(0,0,0,.08)]">
      <header className="flex flex-col sm:flex-row sm:items-center justify-between gap-5 pb-7 border-b border-black/[.075]">
        <div><p className="eyebrow mb-4">Отчёт · IMG_2048.JPG</p><h3 className="text-2xl lg:text-[28px] font-semibold tracking-[-.035em]">Требуется дополнительная проверка</h3></div>
        <div className="flex items-center gap-3 text-sm text-mv-uncertain shrink-0"><span className="w-2 h-2 rounded-full bg-mv-uncertain"/><span>Уверенность</span><strong className="font-semibold">76%</strong></div>
      </header>
      <div className="grid lg:grid-cols-[1.05fr_.95fr] gap-7 lg:gap-12 pt-7">
        <div className="py-1"><h4 className="font-semibold mb-5">Почему такой вывод</h4><p className="text-mv-text-secondary leading-7 max-w-xl">Обнаружены две области со структурными несоответствиями.<br className="hidden sm:block"/> Метаданные сохранены частично.</p></div>
        <aside className="bg-white border border-black/[.07] rounded-[13px] p-6 shadow-[0_1px_2px_rgba(0,0,0,.04),0_12px_28px_rgba(0,0,0,.07)]"><h4 className="font-semibold mb-5">Рекомендация</h4><p className="text-mv-text-secondary leading-7">Сопоставьте файл с первоисточником и запросите оригинал до публикации.</p></aside>
      </div>
    </article>
  </section>

  <section className="container pb-20 lg:pb-32"><div className="grid lg:grid-cols-[.7fr_1.3fr] gap-12"><h2 className="section-title">Вопросы<br/>о проверке</h2><div>{faqs.map((q,i)=><div key={q[0]} className="border-t border-black/[.09]"><button className="w-full py-6 flex items-center justify-between text-left font-medium" onClick={()=>setOpen(open===i?-1:i)} aria-expanded={open===i}>{q[0]}<ChevronDown size={18} className={`transition-transform ${open===i?'rotate-180':''}`}/></button>{open===i&&<p className="text-sm text-mv-text-secondary leading-6 pb-6 pr-12">{q[1]}</p>}</div>)}</div></div></section>

  <section className="container pb-20 lg:pb-32"><div className="soft-card py-20 px-6 text-center"><h2 className="section-title">Проверьте материал до<br/>того, как ему поверят<br/>другие</h2><p className="text-mv-text-secondary mt-6 mb-9">Загрузите файл и получите понятный результат за несколько минут.</p><Link to="/dashboard/check" className="btn-black">Начать проверку <ArrowRight size={16}/></Link></div></section>
  </div>}
