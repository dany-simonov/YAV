import type { ReactNode } from 'react';

export interface LegalSection {
  id: string;
  title: string;
  content: ReactNode;
}

interface LegalDocumentLayoutProps {
  eyebrow: string;
  title: string;
  summary: string;
  status?: string;
  sections: LegalSection[];
  contactEmail: string;
}

export function LegalDocumentLayout({ eyebrow, title, summary, status = 'Проект документа', sections, contactEmail }: LegalDocumentLayoutProps) {
  return <div className="pt-32 pb-24">
    <div className="container">
      <header className="grid lg:grid-cols-[.95fr_1.05fr] gap-10 pb-14 border-b border-black/[.08]"><div><p className="eyebrow mb-6">{eyebrow}</p><h1 className="section-title max-w-3xl">{title}</h1></div><div className="lg:pt-9"><p className="text-lg leading-8 text-mv-text-secondary max-w-xl">{summary}</p><span className="inline-flex mt-6 px-3 py-1.5 rounded-lg bg-mv-uncertain/10 text-mv-uncertain text-xs font-medium">{status}</span></div></header>

      <div className="grid lg:grid-cols-[250px_1fr] gap-10 lg:gap-20 pt-12">
        <aside className="lg:sticky lg:top-28 h-fit"><p className="eyebrow mb-4">Содержание</p><nav className="border-t border-black/[.08]">{sections.map((section,index)=><a key={section.id} href={`#${section.id}`} className="flex gap-3 py-3 border-b border-black/[.08] text-sm text-mv-text-secondary hover:text-black transition-colors"><span className="text-mv-text-muted tabular-nums">{String(index+1).padStart(2,'0')}</span>{section.title}</a>)}</nav></aside>
        <article className="min-w-0">{sections.map((section,index)=><section key={section.id} id={section.id} className="scroll-mt-28 py-9 first:pt-0 border-b border-black/[.08]"><p className="eyebrow mb-4">Раздел {String(index+1).padStart(2,'0')}</p><h2 className="text-2xl sm:text-3xl font-semibold tracking-[-.04em]">{section.title}</h2><div className="mt-6 text-mv-text-secondary leading-7 space-y-4">{section.content}</div></section>)}
          <footer className="pt-10"><p className="text-sm text-mv-text-secondary">Вопросы по документу: <a href={`mailto:${contactEmail}`} className="text-black underline underline-offset-4">{contactEmail}</a></p></footer>
        </article>
      </div>
    </div>
  </div>;
}
