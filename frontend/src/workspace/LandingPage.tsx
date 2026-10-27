import { ArrowRight, Check, FileCheck2, Layers3, ShieldCheck, Users } from 'lucide-react';

export function LandingPage() {
  return <main className="landing-page">
    <section className="landing-hero">
      <div className="landing-copy"><p className="auth-eyebrow">ONE WORKSPACE. EVERY CLIENT.</p><h1>Less spreadsheet work.<br /><em>More clarity.</em></h1><p className="landing-lead">Bring your purchase books and GSTR-2B together in one place to reconcile, review differences, and resolve exceptions.</p><div className="landing-actions"><a className="primary-link" href="#/sign-up">Create your workspace <ArrowRight size={18} /></a><a href="#/sign-in">Already using ALIGNED? Sign in →</a></div><p className="landing-footnote">Built for accounting and GST reconciliation workflows.</p></div>
      <div className="landing-preview" aria-label="Illustration of the reconciliation workflow"><div className="preview-window"><span className="preview-dots">● ● ●</span><span>YOUR RECONCILIATION WORKSPACE</span></div><div className="preview-body"><p className="auth-eyebrow">A CLEARER WAY TO CLOSE</p><h2>Everything in its place.</h2><div className="preview-sources"><div><FileCheck2 /><strong>Purchase books</strong><small>Your source records</small></div><span>+</span><div><Layers3 /><strong>GSTR-2B</strong><small>GST portal export</small></div></div><div className="preview-path"><span /> Compare source records <Check size={16} /></div><div className="preview-path"><span /> Review matches & differences <Check size={16} /></div><div className="preview-path"><span /> Export a clear report <ArrowRight size={16} /></div><p className="preview-caption">Workflow illustration · no sample financial claims</p></div></div>
    </section>
    <section className="landing-features" aria-labelledby="landing-features"><div className="landing-section-heading"><p className="auth-eyebrow">BUILT AROUND YOUR PRACTICE</p><h2 id="landing-features">The work connects. So should your tools.</h2></div><div className="landing-feature-grid">{[
      {icon:FileCheck2,title:'Reconcile with context',body:'Keep source evidence, financial differences and manual review together in one workflow.'},
      {icon:Users,title:'Give every client a home',body:'Organize clients and GST registrations before starting a reconciliation.'},
      {icon:ShieldCheck,title:'Keep access intentional',body:'Protect each workspace and client workflow with authenticated access.'},
    ].map(({icon:Icon,title,body})=><article key={title}><Icon size={24} /><h3>{title}</h3><p>{body}</p></article>)}</div></section>
    <section className="landing-close"><div><p className="auth-eyebrow">YOUR NEXT RECONCILIATION STARTS HERE</p><h2>A little less chasing.<br />A lot more getting things done.</h2></div><a className="primary-link" href="#/sign-up">Get started <ArrowRight size={18} /></a></section>
    <footer className="landing-footer"><strong>aligned.</strong><span>GST reconciliation for your everyday work.</span><span>End-term project deployment.</span></footer>
  </main>;
}
