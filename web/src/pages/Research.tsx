import { useMemo, useRef, useState, type FormEvent } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  CircleAlert,
  ExternalLink,
  FileAudio,
  FileImage,
  FileText,
  FileVideo,
  Link2,
  Map,
  Paperclip,
  SearchCheck,
  ShieldAlert,
  Trash2,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { cn, formatFileSize } from '../lib/utils';
import { useAuthStore } from '../store';

type AnalysisTab = 'complex' | 'quick';
type QuickMode = 'file' | 'text';
type AuthenticityStatus = 'ПРАВДА' | 'ЛОЖЬ' | 'ПОД СОМНЕНИЕМ';

const MAX_FILE_SIZE = 20 * 1024 * 1024;
const ACCEPTED_FILE_TYPES = 'image/*,video/*,audio/*,.pdf,.txt,.doc,.docx';

const statusStyles: Record<AuthenticityStatus, string> = {
  ПРАВДА: 'bg-mv-real/10 text-mv-real border-mv-real/25',
  ЛОЖЬ: 'bg-mv-fake/10 text-mv-fake border-mv-fake/25',
  'ПОД СОМНЕНИЕМ': 'bg-mv-uncertain/10 text-mv-uncertain border-mv-uncertain/25',
};

function StatusBadge({ status }: { status: AuthenticityStatus }) {
  return (
    <span className={cn('inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-bold tracking-[.04em]', statusStyles[status])}>
      <span className="w-2 h-2 rounded-full bg-current" />
      {status}
    </span>
  );
}

function fileIcon(file: File) {
  if (file.type.startsWith('image/')) return <FileImage size={18} />;
  if (file.type.startsWith('video/')) return <FileVideo size={18} />;
  if (file.type.startsWith('audio/')) return <FileAudio size={18} />;
  return <FileText size={18} />;
}

function AuthenticityScale({ value }: { value: number }) {
  return (
    <div aria-label={`Индекс подлинности: ${value}%`}>
      <div className="flex items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Индекс подлинности</p>
          <p className="mt-2 text-sm text-mv-text-secondary">Шкала от 0 до 100%</p>
        </div>
        <strong className="text-4xl sm:text-5xl leading-none tracking-[-.06em]">{value}%</strong>
      </div>
      <div className="relative mt-6 h-3 overflow-hidden rounded-full bg-gradient-to-r from-mv-fake via-mv-uncertain to-mv-real">
        <span
          className="absolute top-1/2 h-6 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full border border-black/25 bg-white shadow"
          style={{ left: `${value}%` }}
        />
      </div>
      <div className="mt-2 flex justify-between text-[11px] font-semibold text-mv-text-muted"><span>0</span><span>50</span><span>100</span></div>
    </div>
  );
}

function ResearchResultPreview() {
  const xaiRows = [
    { icon: FileText, title: 'Факты и утверждения в тексте', result: '2 утверждения требуют первоисточника', tone: 'text-mv-uncertain bg-mv-uncertain/10' },
    { icon: SearchCheck, title: 'Признаки ИИ-генерации текста', result: 'Выраженных признаков не обнаружено', tone: 'text-mv-real bg-mv-real/10' },
    { icon: FileImage, title: 'Фото и видео: проверка на дипфейки', result: '1 изображение требует ручной оценки', tone: 'text-mv-uncertain bg-mv-uncertain/10' },
    { icon: Link2, title: 'Ссылки на первоисточники', result: '3 источника найдены, 1 ссылка недоступна', tone: 'text-mv-text bg-black/[.045]' },
  ];

  return (
    <section className="mt-8 soft-card overflow-hidden" aria-labelledby="result-heading">
      <div className="grid lg:grid-cols-[1.05fr_.95fr]">
        <div className="p-6 sm:p-9 lg:p-10 border-b lg:border-b-0 lg:border-r border-black/[.08]">
          <p className="eyebrow">Демонстрационная структура результата</p>
          <div className="mt-5 flex flex-col sm:flex-row sm:items-center gap-4 justify-between">
            <h2 id="result-heading" className="text-3xl sm:text-4xl font-semibold tracking-[-.045em]">Комплексный вывод</h2>
            <StatusBadge status="ПОД СОМНЕНИЕМ" />
          </div>
          <p className="mt-5 max-w-xl text-mv-text-secondary leading-7">Материал содержит утверждения и медиакомпоненты, которые требуют сверки с первоисточниками. Статус является вероятностной оценкой, а не гарантией.</p>
          <div className="mt-10"><AuthenticityScale value={64} /></div>
          <p className="mt-7 text-xs leading-5 text-mv-text-muted">Значения в этом блоке демонстрируют будущий формат ответа. Они не относятся к загруженному пользователем материалу.</p>
        </div>

        <div className="p-6 sm:p-9 lg:p-10 bg-black/[.018]">
          <p className="eyebrow">Explainable AI · детализация</p>
          <h3 className="mt-4 text-2xl font-semibold tracking-[-.035em]">Почему система сделала вывод</h3>
          <div className="mt-7 divide-y divide-black/[.08]">
            {xaiRows.map(({ icon: Icon, title, result, tone }) => (
              <div key={title} className="py-5 first:pt-0 grid grid-cols-[40px_1fr] gap-4">
                <span className={cn('w-10 h-10 rounded-xl flex items-center justify-center', tone)}><Icon size={18} /></span>
                <div><p className="font-semibold leading-6">{title}</p><p className="mt-1 text-sm leading-6 text-mv-text-secondary">{result}</p></div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

function ArcticAnalysisWidget() {
  const { user } = useAuthStore();
  const [tab, setTab] = useState<AnalysisTab>('complex');
  const [quickMode, setQuickMode] = useState<QuickMode>('file');
  const [article, setArticle] = useState('');
  const [links, setLinks] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [quickText, setQuickText] = useState('');
  const [quickFile, setQuickFile] = useState<File | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const complexFileInput = useRef<HTMLInputElement>(null);
  const quickFileInput = useRef<HTMLInputElement>(null);

  const validLinks = useMemo(() => links.split(/\n|,/).map((item) => item.trim()).filter(Boolean), [links]);
  const verificationBlocked = !!user && !user.emailVerification;

  const validateFiles = (incoming: File[]) => {
    const oversized = incoming.find((file) => file.size > MAX_FILE_SIZE);
    if (oversized) {
      setError(`Файл «${oversized.name}» больше 20 МБ.`);
      return [];
    }
    setError(null);
    return incoming;
  };

  const submitComplex = (event: FormEvent) => {
    event.preventDefault();
    setNotice(null);
    if (!user || verificationBlocked) return;
    if (!article.trim()) {
      setError('Добавьте текст статьи или поста.');
      return;
    }
    setError(null);
    setNotice(`Пакет подготовлен: текст, ${files.length} файл(а/ов), ${validLinks.length} ссылок. Для запуска анализа необходимо подключить серверный endpoint комплексной проверки.`);
  };

  const submitQuick = (event: FormEvent) => {
    event.preventDefault();
    setNotice(null);
    if (!user) return;
    if (quickMode === 'file' && !quickFile) {
      setError('Выберите один файл для проверки.');
      return;
    }
    if (quickMode === 'text' && !quickText.trim()) {
      setError('Введите текст для проверки.');
      return;
    }
    setError(null);
    setNotice('Быстрая проверка уже подключена в рабочей области. Перейдите туда, чтобы отправить материал действующему анализатору.');
  };

  return (
    <section className="mt-14" aria-labelledby="analysis-widget-title">
      <div className="soft-card overflow-hidden">
        <div className="p-5 sm:p-7 border-b border-black/[.08] flex flex-col lg:flex-row lg:items-center lg:justify-between gap-5">
          <div><p className="eyebrow">Мультимодальная проверка</p><h2 id="analysis-widget-title" className="mt-3 text-2xl sm:text-3xl font-semibold tracking-[-.04em]">Проверьте материал целиком или по одному компоненту</h2></div>
          <div className="inline-flex self-start p-1 rounded-xl bg-black/[.04] border border-black/[.06]">
            <button type="button" onClick={() => { setTab('complex'); setError(null); setNotice(null); }} className={cn('px-4 py-3 rounded-[9px] text-sm font-semibold transition', tab === 'complex' ? 'bg-white shadow-sm text-black' : 'text-mv-text-secondary')}>Комплексный анализ</button>
            <button type="button" onClick={() => { setTab('quick'); setError(null); setNotice(null); }} className={cn('px-4 py-3 rounded-[9px] text-sm font-semibold transition', tab === 'quick' ? 'bg-white shadow-sm text-black' : 'text-mv-text-secondary')}>Быстрая проверка</button>
          </div>
        </div>

        {tab === 'complex' ? (
          <form onSubmit={submitComplex} className="p-5 sm:p-8 lg:p-10">
            <div className="max-w-3xl"><p className="eyebrow">Основная вкладка</p><h3 className="mt-3 text-2xl sm:text-3xl font-semibold tracking-[-.04em]">Комплексный анализ статьи/поста</h3><p className="mt-4 leading-7 text-mv-text-secondary">Вставьте полный материал и приложите найденные в нём фото, видео, аудио и ссылки. Компоненты будут разобраны в одном отчёте.</p></div>
            <label className="block mt-8 text-sm font-semibold">Текст статьи или поста
              <textarea value={article} onChange={(event) => setArticle(event.target.value)} rows={9} placeholder="Вставьте сюда полный текст материала…" className="mt-2 w-full resize-y rounded-xl border border-black/[.1] bg-white p-4 leading-7 shadow-sm focus:border-black outline-none" />
            </label>

            <div className="mt-5 grid md:grid-cols-2 gap-5">
              <div>
                <p className="text-sm font-semibold">Фото, видео и аудио</p>
                <input ref={complexFileInput} type="file" multiple accept={ACCEPTED_FILE_TYPES} className="sr-only" onChange={(event) => {
                  const selected = validateFiles(Array.from(event.target.files ?? []));
                  if (selected.length) setFiles((current) => [...current, ...selected].slice(0, 10));
                  event.target.value = '';
                }} />
                <button type="button" onClick={() => complexFileInput.current?.click()} className="mt-2 w-full min-h-[116px] rounded-xl border border-dashed border-black/20 bg-black/[.018] hover:bg-black/[.035] transition flex flex-col items-center justify-center gap-2 text-sm font-semibold"><Paperclip size={20} /><span>Добавить файлы · до 20 МБ каждый</span></button>
              </div>
              <label className="block text-sm font-semibold">Ссылки на источники
                <textarea value={links} onChange={(event) => setLinks(event.target.value)} rows={4} placeholder={'https://example.ru/source\nОдна ссылка на строку'} className="mt-2 w-full min-h-[116px] resize-none rounded-xl border border-black/[.1] bg-white p-4 font-normal leading-6 shadow-sm focus:border-black outline-none" />
              </label>
            </div>

            {files.length > 0 && <div className="mt-5 grid sm:grid-cols-2 gap-2">{files.map((file, index) => <div key={`${file.name}-${file.lastModified}-${index}`} className="flex items-center gap-3 rounded-xl border border-black/[.08] bg-white p-3"><span className="text-mv-text-secondary">{fileIcon(file)}</span><span className="min-w-0 flex-1"><span className="block truncate text-sm font-semibold">{file.name}</span><span className="text-xs text-mv-text-muted">{formatFileSize(file.size)}</span></span><button type="button" aria-label={`Удалить ${file.name}`} onClick={() => setFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))} className="p-2 rounded-lg hover:bg-black/5"><Trash2 size={16} /></button></div>)}</div>}

            <AccessAndSubmit userExists={!!user} verificationBlocked={verificationBlocked} submitLabel="Запустить комплексный анализ" loginReturnPath="/research/arctic" />
            <FormFeedback error={error} notice={notice} quickLink={false} />
          </form>
        ) : (
          <form onSubmit={submitQuick} className="p-5 sm:p-8 lg:p-10">
            <div className="max-w-3xl"><p className="eyebrow">Вторая вкладка</p><h3 className="mt-3 text-2xl sm:text-3xl font-semibold tracking-[-.04em]">Быстрая проверка файла/текста</h3><p className="mt-4 leading-7 text-mv-text-secondary">Отправьте один файл или отдельный текст в действующий сценарий проверки ЯВЬ.</p></div>
            <div className="mt-7 inline-flex p-1 rounded-xl bg-black/[.04] border border-black/[.06]">
              {(['file', 'text'] as QuickMode[]).map((mode) => <button key={mode} type="button" onClick={() => { setQuickMode(mode); setError(null); setNotice(null); }} className={cn('min-w-24 px-4 py-2.5 rounded-[9px] text-sm font-semibold', quickMode === mode ? 'bg-white shadow-sm' : 'text-mv-text-secondary')}>{mode === 'file' ? 'Файл' : 'Текст'}</button>)}
            </div>

            {quickMode === 'file' ? <div className="mt-5">
              <input ref={quickFileInput} type="file" accept={ACCEPTED_FILE_TYPES} className="sr-only" onChange={(event) => setQuickFile(validateFiles(Array.from(event.target.files ?? []))[0] ?? null)} />
              <button type="button" onClick={() => quickFileInput.current?.click()} className="w-full min-h-[220px] rounded-2xl border border-dashed border-black/20 bg-black/[.018] hover:bg-black/[.035] transition flex flex-col items-center justify-center text-center px-6"><Paperclip size={24} /><span className="mt-4 text-lg font-semibold">{quickFile ? quickFile.name : 'Перетащите файл или выберите на устройстве'}</span><span className="mt-2 text-sm text-mv-text-secondary">Изображение, видео, аудио или документ · до 20 МБ</span></button>
            </div> : <textarea value={quickText} onChange={(event) => setQuickText(event.target.value)} rows={9} placeholder="Введите текст для проверки…" className="mt-5 w-full resize-y rounded-xl border border-black/[.1] bg-white p-4 leading-7 shadow-sm focus:border-black outline-none" />}

            <AccessAndSubmit userExists={!!user} verificationBlocked={false} submitLabel="Продолжить быструю проверку" loginReturnPath="/research/arctic" />
            <FormFeedback error={error} notice={notice} quickLink />
          </form>
        )}
      </div>
    </section>
  );
}

function AccessAndSubmit({ userExists, verificationBlocked, submitLabel, loginReturnPath }: { userExists: boolean; verificationBlocked: boolean; submitLabel: string; loginReturnPath: string }) {
  if (!userExists) return <div className="mt-7 flex flex-col sm:flex-row sm:items-center gap-4"><Link to="/login" state={{ from: { pathname: loginReturnPath } }} className="btn-black">Войти для проверки</Link><p className="text-sm text-mv-text-secondary">Для отправки материалов требуется аккаунт.</p></div>;
  if (verificationBlocked) return <div className="mt-7 rounded-xl border border-mv-uncertain/25 bg-mv-uncertain/5 p-5 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4"><div className="flex gap-3"><ShieldAlert className="shrink-0 text-mv-uncertain" size={21} /><p className="text-sm leading-6"><strong className="block">Подтвердите e-mail</strong><span className="text-mv-text-secondary">Комплексные мультимодальные запросы заблокированы до подтверждения.</span></p></div><Link to="/verify-email/pending" className="btn-light shrink-0">Подтвердить</Link></div>;
  return <button type="submit" className="btn-black mt-7">{submitLabel}<ArrowRight size={16} /></button>;
}

function FormFeedback({ error, notice, quickLink }: { error: string | null; notice: string | null; quickLink: boolean }) {
  return <>{error && <p role="alert" className="mt-5 flex gap-2 text-sm text-mv-fake"><CircleAlert className="shrink-0" size={18} />{error}</p>}{notice && <div role="status" className="mt-5 rounded-xl border border-black/[.09] bg-black/[.025] p-5"><p className="flex gap-2 text-sm leading-6"><CheckCircle2 className="shrink-0 text-mv-real" size={18} />{notice}</p>{quickLink && <Link to="/dashboard/check" className="btn-light mt-4">Открыть быструю проверку <ArrowRight size={15} /></Link>}</div>}</>;
}

function CaseStudySlots() {
  return (
    <section className="mt-24" aria-labelledby="cases-heading">
      <div className="grid lg:grid-cols-[.8fr_1.2fr] gap-8 lg:gap-16 items-end"><div><p className="eyebrow">Case Studies</p><h2 id="cases-heading" className="section-title mt-5">Разобранные арктические материалы</h2></div><p className="text-lg leading-8 text-mv-text-secondary">Здесь появятся реальные кейсы с источниками, ходом проверки и готовыми вердиктами. Карточки не заполнены демонстрационными историями, чтобы не выдавать вымышленные материалы за реальные.</p></div>
      <div className="mt-10 grid md:grid-cols-2 gap-4">{[1, 2, 3, 4].map((number) => <article key={number} className="min-h-[210px] rounded-2xl border border-dashed border-black/20 p-6 sm:p-8 flex flex-col justify-between"><div><p className="eyebrow">Кейс {String(number).padStart(2, '0')}</p><h3 className="mt-5 text-xl font-semibold tracking-[-.025em]">Материал будет добавлен</h3><p className="mt-3 text-sm leading-6 text-mv-text-secondary">Место для подготовленного кейса, первоисточников и вердикта «ПРАВДА» или «ЛОЖЬ».</p></div><span className="mt-8 text-xs font-semibold text-mv-text-muted">ОЖИДАЕТ МАТЕРИАЛ</span></article>)}</div>
    </section>
  );
}

export function Research() {
  return <div className="pt-32 pb-24"><section className="container"><p className="eyebrow mb-6">Исследовательское направление ЯВЬ</p><h1 className="section-title max-w-3xl">Исследования цифрового контента</h1><p className="mt-7 text-lg leading-8 text-mv-text-secondary max-w-2xl">Материалы о происхождении цифрового контента, проверке медиаданных и применении воспроизводимых методов анализа.</p><div className="mt-16 border-t border-black/[.09]"><Link to="/research/arctic" className="group grid md:grid-cols-[180px_1fr_auto] items-start md:items-center gap-6 py-9 border-b border-black/[.09]"><span className="eyebrow flex items-center gap-2"><Map size={15}/> Направление 01</span><div><h2 className="text-2xl font-semibold tracking-[-.035em]">Медиаполе АЗРФ</h2><p className="mt-3 text-mv-text-secondary leading-7 max-w-2xl">Исследование информационной достоверности и детекция ИИ-контента в арктическом медиаполе.</p></div><span className="w-11 h-11 rounded-xl border border-black/[.1] bg-white shadow-sm flex items-center justify-center group-hover:-translate-y-0.5 transition-transform"><ArrowRight size={18}/></span></Link></div></section></div>;
}

export function ArcticResearch() {
  return (
    <div className="pt-32 pb-28">
      <article className="container">
        <Link to="/research" className="eyebrow inline-flex items-center gap-2 hover:text-black transition-colors"><ArrowLeft size={14} /> Исследования</Link>
        <header className="mt-8 grid lg:grid-cols-[1fr_300px] gap-10 lg:gap-20 items-end">
          <div><p className="eyebrow mb-5">Грантовый модуль ПОРА · АЗРФ</p><h1 className="text-[clamp(2.55rem,5.4vw,4.7rem)] leading-[.99] tracking-[-.055em] font-semibold max-w-5xl">Исследование информационной достоверности и детекция ИИ-контента в медиаполе АЗРФ</h1></div>
          <aside className="border-l border-black/[.1] pl-6 pb-1"><p className="eyebrow">Направление</p><p className="mt-3 font-semibold">Научно-прикладное исследование</p><p className="mt-3 text-sm leading-6 text-mv-text-secondary">Мультимодальный анализ публикаций, медиаматериалов и источников.</p></aside>
        </header>

        <ArcticAnalysisWidget />
        <ResearchResultPreview />

        <section className="mt-8 grid sm:grid-cols-3 gap-3" aria-label="Возможные статусы результата">
          {(['ПРАВДА', 'ЛОЖЬ', 'ПОД СОМНЕНИЕМ'] as AuthenticityStatus[]).map((status) => <div key={status} className="rounded-2xl border border-black/[.08] bg-white p-5"><StatusBadge status={status} /><p className="mt-4 text-sm leading-6 text-mv-text-secondary">{status === 'ПРАВДА' ? 'Источники и признаки подтверждают материал.' : status === 'ЛОЖЬ' ? 'Обнаружены существенные опровержения или признаки подделки.' : 'Данных недостаточно для однозначного вывода.'}</p></div>)}
        </section>

        <CaseStudySlots />

        <section className="mt-24 rounded-2xl bg-black text-white p-7 sm:p-10 grid md:grid-cols-[1fr_auto] gap-8 items-center"><div><p className="eyebrow !text-white/50">Методология</p><h2 className="mt-4 text-3xl font-semibold tracking-[-.04em]">Результат остаётся объяснимым</h2><p className="mt-4 max-w-2xl text-white/65 leading-7">Каждый статус сопровождается индексом, покомпонентным анализом и ссылками на проверяемые первоисточники.</p></div><Link to="/docs" className="btn-light">Открыть документацию <ExternalLink size={15} /></Link></section>
      </article>
    </div>
  );
}
