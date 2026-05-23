<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from '@/composables/useI18n'
import { useAppStore } from '@/stores/app'

const router = useRouter()
const appStore = useAppStore()
const { t } = useI18n()

async function handleStart() {
  localStorage.setItem('arf_seen_welcome', '1')
  await appStore.checkConfigStatus()
  if (appStore.configStatus?.configured) {
    router.replace('/')
  } else {
    router.replace('/config')
  }
}

function scrollToSection(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
}

// ── Scroll reveal ──────────────────────────────
const revealRefs = ref<HTMLElement[]>([])

function setRevealRef(el: unknown) {
  if (el instanceof HTMLElement) revealRefs.value.push(el)
}

let observer: IntersectionObserver | null = null

onMounted(() => {
  observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('revealed')
        }
      })
    },
    { threshold: 0.12, rootMargin: '0px 0px -40px 0px' }
  )
  revealRefs.value.forEach((el) => observer!.observe(el))
})

onUnmounted(() => {
  observer?.disconnect()
})
</script>

<template>
  <div id="welcome-page">
    <!-- Grain overlay -->
    <div class="grain-overlay"></div>

    <!-- ── Top Bar ──────────────────────────────── -->
    <div class="welcome-topbar">
      <span class="topbar-brand">ARF</span>
      <div class="topbar-right">
        <select v-model="appStore.language" @change="appStore.setLanguage(appStore.language)" class="lang-select">
          <option value="zh">中文</option>
          <option value="en">English</option>
        </select>
      </div>
    </div>

    <!-- ── Hero ─────────────────────────────────── -->
    <section class="hero-section">
      <div class="hero-glow"></div>
      <h1 class="hero-title">ARF</h1>
      <p class="hero-subtitle">Agent Resources & RunTime FrameWork</p>
      <p class="hero-tagline">{{ t('welcome.heroTagline') }}</p>
      <p class="hero-desc">{{ t('welcome.heroDescription') }}</p>
      <div class="hero-actions">
        <button class="btn-hero-primary" @click="handleStart">
          {{ t('welcome.startBtn') }}
        </button>
        <button class="btn-hero-secondary" @click="scrollToSection('tribute-section')">
          {{ t('welcome.learnMore') }}
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
        </button>
      </div>
    </section>

    <!-- ── Tribute ──────────────────────────────── -->
    <section id="tribute-section" class="content-section" :ref="setRevealRef">
      <div class="section-ornament"></div>
      <h2 class="section-title">{{ t('welcome.tributeTitle') }}</h2>
      <div class="tribute-card">
        <div class="tribute-card-glow"></div>
        <blockquote class="tribute-quote">
          <span class="tribute-quote-open">&ldquo;</span>
          <span class="tribute-quote-text">{{ t('welcome.tributeQuote') }}</span>
          <span class="tribute-quote-close">&rdquo;</span>
        </blockquote>
        <p class="tribute-attr">{{ t('welcome.tributeQuoteAttr') }}</p>
        <div class="tribute-divider"></div>
        <p class="tribute-body">{{ t('welcome.tributeBody') }}</p>
        <div class="tribute-links">
          <a href="https://mp.weixin.qq.com/s/8bxXqS2R8Fx5-1TLDBiEDg" target="_blank" rel="noopener" class="tribute-link">
            {{ t('welcome.tributeLinkDsV4') }} <span class="tribute-link-arrow">&#8599;</span>
          </a>
          <a href="https://www.deepseek.com/" target="_blank" rel="noopener" class="tribute-link">
            {{ t('welcome.tributeLinkDsSite') }} <span class="tribute-link-arrow">&#8599;</span>
          </a>
        </div>
      </div>
    </section>

    <!-- ── Vision & Mission ─────────────────────── -->
    <section class="content-section" :ref="setRevealRef">
      <div class="section-ornament"></div>
      <h2 class="section-title">{{ t('welcome.visionTitle') }}</h2>
      <p class="vision-lead">{{ t('welcome.visionIntro') }}</p>
      <div class="vision-grid">
        <div class="vision-card">
          <div class="vision-card-badge">{{ t('welcome.visionCardTitle') }}</div>
          <p>{{ t('welcome.visionCardBody') }}</p>
        </div>
        <div class="vision-card">
          <div class="vision-card-badge">{{ t('welcome.missionCardTitle') }}</div>
          <p>{{ t('welcome.missionCardBody') }}</p>
        </div>
      </div>
    </section>

    <!-- ── Everything as Resource ───────────────── -->
    <section class="content-section" :ref="setRevealRef">
      <div class="section-ornament"></div>
      <h2 class="section-title">{{ t('welcome.eorTitle') }}</h2>
      <p class="eor-lead">{{ t('welcome.eorIntro') }}</p>
      <div class="eor-grid">
        <div class="eor-card" style="--i: 0">
          <div class="eor-card-icon">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
          </div>
          <div class="eor-card-text">
            <h3>{{ t('welcome.eorModelTitle') }}</h3>
            <p>{{ t('welcome.eorModelDesc') }}</p>
          </div>
        </div>
        <div class="eor-card" style="--i: 1">
          <div class="eor-card-icon">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20.24 12.24a6 6 0 0 0-8.49-8.49L5 10.5V19h8.5z"/><line x1="16" y1="8" x2="2" y2="22"/><line x1="17.5" y1="15" x2="9" y2="15"/></svg>
          </div>
          <div class="eor-card-text">
            <h3>{{ t('welcome.eorSkillTitle') }}</h3>
            <p>{{ t('welcome.eorSkillDesc') }}</p>
          </div>
        </div>
        <div class="eor-card" style="--i: 2">
          <div class="eor-card-icon">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
          </div>
          <div class="eor-card-text">
            <h3>{{ t('welcome.eorToolTitle') }}</h3>
            <p>{{ t('welcome.eorToolDesc') }}</p>
          </div>
        </div>
        <div class="eor-card" style="--i: 3">
          <div class="eor-card-icon">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
          </div>
          <div class="eor-card-text">
            <h3>{{ t('welcome.eorMemoryTitle') }}</h3>
            <p>{{ t('welcome.eorMemoryDesc') }}</p>
          </div>
        </div>
      </div>
      <p class="eor-conclusion">{{ t('welcome.eorConclusion') }}</p>
    </section>

    <!-- ── Goals ────────────────────────────────── -->
    <section class="content-section" :ref="setRevealRef">
      <div class="section-ornament"></div>
      <h2 class="section-title">{{ t('welcome.goalsTitle') }}</h2>
      <div class="goals-grid">
        <div class="goal-card" style="--i: 0">
          <div class="goal-number">01</div>
          <h3>{{ t('welcome.goal1Title') }}</h3>
          <p>{{ t('welcome.goal1Desc') }}</p>
        </div>
        <div class="goal-card" style="--i: 1">
          <div class="goal-number">02</div>
          <h3>{{ t('welcome.goal2Title') }}</h3>
          <p>{{ t('welcome.goal2Desc') }}</p>
        </div>
        <div class="goal-card" style="--i: 2">
          <div class="goal-number">03</div>
          <h3>{{ t('welcome.goal3Title') }}</h3>
          <p>{{ t('welcome.goal3Desc') }}</p>
        </div>
        <div class="goal-card" style="--i: 3">
          <div class="goal-number">04</div>
          <h3>{{ t('welcome.goal4Title') }}</h3>
          <p>{{ t('welcome.goal4Desc') }}</p>
        </div>
      </div>
    </section>

    <!-- ── CTA ──────────────────────────────────── -->
    <section class="cta-section" :ref="setRevealRef">
      <div class="cta-inner">
        <p class="cta-quote">{{ t('welcome.ctaQuote') }}</p>
        <p class="cta-text">{{ t('welcome.ctaText') }}</p>
        <button class="btn-hero-primary" @click="handleStart">
          {{ t('welcome.startBtn') }}
        </button>
      </div>
    </section>

    <!-- Footer -->
    <footer class="welcome-footer">
      <p>{{ t('welcome.footer') }}</p>
    </footer>
  </div>
</template>

<style scoped>
/* ═══════════════════════════════════════════════════════════════════════════
   Welcome Page — "Veiled Cosmos" Aesthetic
   Editorial sci-fi: deep atmosphere, literary typography, restrained accent
   ═══════════════════════════════════════════════════════════════════════════ */

#welcome-page {
  min-height: 100vh;
  background: #080810;
  display: flex;
  flex-direction: column;
  position: relative;
}

/* ── Grain texture overlay ────────────────────── */
.grain-overlay {
  position: fixed; inset: 0; pointer-events: none; z-index: 0; opacity: 0.035;
  background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.72' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)'/%3E%3C/svg%3E");
}

/* ── Top Bar ──────────────────────────────────── */
.welcome-topbar {
  position: fixed; top: 0; left: 0; right: 0; height: 48px; z-index: 10;
  background: rgba(8,8,16,0.88); backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border-bottom: 1px solid rgba(255,255,255,0.05);
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 24px;
}
.topbar-brand { font-weight: 700; color: var(--text-primary); letter-spacing: 1px; font-size: 15px; }
.topbar-right { display: flex; align-items: center; gap: 14px; }
.lang-select {
  background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.14);
  border-radius: var(--radius-sm); color: var(--text-primary);
  font-size: 14px; font-weight: 500; padding: 6px 12px; cursor: pointer; outline: none;
  transition: border-color var(--transition), background var(--transition);
}
.lang-select:hover { background: rgba(255,255,255,0.12); }
.lang-select:focus { border-color: var(--accent); }
.lang-select option { background: #141428; color: var(--text-primary); }

/* ── Hero ─────────────────────────────────────── */
.hero-section {
  position: relative; z-index: 1;
  display: flex; flex-direction: column; align-items: center;
  text-align: center; padding: 140px 24px 100px;
  animation: heroEnter 0.9s cubic-bezier(0.22, 0.61, 0.36, 1);
}
@keyframes heroEnter {
  from { opacity: 0; transform: translateY(28px); }
  to   { opacity: 1; transform: translateY(0); }
}
.hero-glow {
  position: absolute; top: -120px; left: 50%; transform: translateX(-50%);
  width: 700px; height: 500px;
  background: radial-gradient(ellipse at 50% 50%, rgba(99,102,241,0.07) 0%, transparent 70%);
  pointer-events: none;
}
.hero-title {
  font-size: 52px; font-weight: 800; color: var(--text-primary);
  letter-spacing: -1.5px; margin-bottom: 8px;
  background: linear-gradient(180deg, #ffffff 0%, #c4c4d4 100%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  background-clip: text;
}
.hero-subtitle {
  font-size: 13px; color: var(--text-muted); letter-spacing: 4px;
  text-transform: uppercase; margin-bottom: 40px; font-weight: 500;
}
.hero-tagline {
  font-size: 20px; font-weight: 500; color: var(--text-primary);
  margin-bottom: 16px; max-width: 720px; line-height: 1.5;
  letter-spacing: -0.3px;
}
.hero-desc {
  font-size: 15px; color: var(--text-secondary); line-height: 1.85;
  max-width: 720px; margin-bottom: 44px;
}
.hero-actions { display: flex; gap: 14px; align-items: center; }
.btn-hero-primary {
  display: inline-flex; align-items: center; justify-content: center;
  padding: 15px 52px; border: none; border-radius: var(--radius-full);
  background: var(--accent-gradient); color: var(--text-on-accent);
  font-size: 15px; font-weight: 700; cursor: pointer;
  transition: all 0.25s ease;
  box-shadow: 0 4px 24px rgba(99,102,241,0.25);
}
.btn-hero-primary:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 32px rgba(99,102,241,0.4);
}
.btn-hero-secondary {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 15px 36px; border: 1px solid rgba(255,255,255,0.08);
  border-radius: var(--radius-full);
  background: transparent; color: var(--text-secondary); font-size: 15px;
  font-weight: 500; cursor: pointer; transition: all 0.25s ease;
}
.btn-hero-secondary:hover { border-color: var(--accent); color: var(--text-primary); }
.btn-hero-secondary svg { transition: transform 0.25s ease; }
.btn-hero-secondary:hover svg { transform: translateY(2px); }

/* ── Content Sections ─────────────────────────── */
.content-section {
  position: relative; z-index: 1;
  max-width: 1100px; margin: 0 auto; padding: 0 32px 100px; width: 100%;
  opacity: 0; transform: translateY(32px);
  transition: opacity 0.7s cubic-bezier(0.22, 0.61, 0.36, 1),
              transform 0.7s cubic-bezier(0.22, 0.61, 0.36, 1);
}
.content-section.revealed {
  opacity: 1; transform: translateY(0);
}

/* Section ornament — subtle accent dot + line */
.section-ornament {
  width: 28px; height: 3px; background: var(--accent); border-radius: 2px;
  margin-bottom: 28px; opacity: 0.5;
}
.section-title {
  font-size: 22px; font-weight: 700; color: var(--text-primary);
  margin: 0 0 16px; letter-spacing: -0.3px;
}
/* ── Tribute ──────────────────────────────────── */
.tribute-card {
  position: relative;
  background: linear-gradient(135deg, #10101c 0%, #0c0c1a 100%);
  border: 1px solid rgba(255,255,255,0.05);
  border-radius: var(--radius-xl); padding: 48px 44px;
  overflow: hidden;
}
.tribute-card-glow {
  position: absolute; top: -60px; right: -40px;
  width: 200px; height: 200px;
  background: radial-gradient(circle, rgba(99,102,241,0.06) 0%, transparent 70%);
  pointer-events: none;
}
.tribute-quote {
  margin: 0 0 16px; padding: 0; border: none;
  display: flex; align-items: flex-start; gap: 4px;
}
.tribute-quote-open, .tribute-quote-close {
  font-family: Georgia, 'Times New Roman', 'Noto Serif SC', serif;
  font-size: 56px; font-weight: 700; color: var(--accent);
  line-height: 1; flex-shrink: 0; opacity: 0.5;
  user-select: none;
}
.tribute-quote-open { margin-top: -6px; }
.tribute-quote-close { align-self: flex-end; margin-bottom: -16px; margin-left: 4px; }
.tribute-quote-text {
  font-family: Georgia, 'Times New Roman', 'Noto Serif SC', serif;
  font-size: 24px; font-weight: 400; color: #e8e8f0;
  line-height: 1.55; font-style: italic;
}
.tribute-attr {
  font-size: 13px; color: var(--accent); margin: 0 0 28px;
  font-weight: 500; letter-spacing: 0.4px; padding-left: 4px;
}
.tribute-divider {
  width: 48px; height: 1px; background: rgba(255,255,255,0.08);
  margin-bottom: 24px;
}
.tribute-body {
  font-size: 15px; color: #9d9db8; line-height: 1.9;
  margin: 0 0 32px; max-width: 700px;
}
.tribute-links { display: flex; gap: 14px; flex-wrap: wrap; }
.tribute-link {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 13px; color: var(--accent); text-decoration: none; font-weight: 500;
  padding: 8px 20px; border-radius: var(--radius-full);
  border: 1px solid rgba(99,102,241,0.18);
  transition: all 0.25s ease;
}
.tribute-link:hover { background: rgba(99,102,241,0.08); border-color: rgba(99,102,241,0.4); }
.tribute-link-arrow { font-size: 12px; transition: transform 0.25s ease; }
.tribute-link:hover .tribute-link-arrow { transform: translate(2px, -2px); }

/* ── Vision & Mission ─────────────────────────── */
.vision-lead {
  font-size: 16px; color: var(--text-secondary); line-height: 1.8;
  margin: 0 0 28px; max-width: 680px;
}
.vision-grid {
  display: grid; grid-template-columns: repeat(2, 1fr); gap: 18px;
}
.vision-card {
  background: #0e0e1e; border: 1px solid rgba(255,255,255,0.04);
  border-radius: var(--radius-lg); padding: 32px;
  transition: border-color 0.3s ease, background 0.3s ease;
}
.vision-card:hover {
  border-color: rgba(99,102,241,0.2);
  background: #101024;
}
.vision-card-badge {
  display: inline-block; font-size: 11px; font-weight: 700;
  color: var(--accent); letter-spacing: 1.5px; text-transform: uppercase;
  padding: 4px 12px; border-radius: var(--radius-full);
  background: rgba(99,102,241,0.08); margin-bottom: 18px;
}
.vision-card p {
  font-size: 14px; color: #9d9db8; line-height: 1.85; margin: 0;
}

/* ── Everything as Resource ───────────────────── */
.eor-lead {
  font-size: 15px; color: var(--text-secondary); line-height: 1.8;
  margin: 0 0 28px; max-width: 680px;
}
.eor-grid {
  display: flex; flex-direction: column; gap: 10px; margin-bottom: 28px;
}
.eor-card {
  display: flex; gap: 18px; align-items: flex-start;
  background: #0e0e1e; border: 1px solid rgba(255,255,255,0.04);
  border-radius: var(--radius-lg); padding: 22px 28px;
  transition: border-color 0.3s ease, background 0.3s ease, transform 0.3s ease;
  transition-delay: calc(var(--i) * 0.06s);
  opacity: 0; transform: translateX(-16px);
}
.content-section.revealed .eor-card {
  opacity: 1; transform: translateX(0);
  transition: opacity 0.5s ease, transform 0.5s ease, border-color 0.3s ease, background 0.3s ease;
  transition-delay: calc(0.15s + var(--i) * 0.1s);
}
.eor-card:hover {
  border-color: rgba(99,102,241,0.2);
  background: #101024;
}
.eor-card-icon { color: var(--accent); flex-shrink: 0; margin-top: 3px; opacity: 0.7; }
.eor-card-text h3 {
  font-size: 15px; font-weight: 600; color: #d4d4e4; margin-bottom: 4px;
}
.eor-card-text p {
  font-size: 14px; color: #8a8aa8; line-height: 1.75; margin: 0;
}
.eor-conclusion {
  font-size: 15px; color: #c8c8d8; font-weight: 500;
  line-height: 1.8; margin: 0; padding: 22px 28px;
  background: rgba(99,102,241,0.04); border-radius: var(--radius-lg);
  border-left: 2px solid var(--accent); font-style: italic;
}

/* ── Goals ────────────────────────────────────── */
.goals-grid {
  display: grid; grid-template-columns: repeat(2, 1fr); gap: 18px;
}
.goal-card {
  background: #0e0e1e; border: 1px solid rgba(255,255,255,0.04);
  border-radius: var(--radius-lg); padding: 28px 30px;
  transition: border-color 0.3s ease, background 0.3s ease, transform 0.3s ease;
  transition-delay: calc(var(--i) * 0.05s);
  opacity: 0; transform: translateY(16px);
}
.content-section.revealed .goal-card {
  opacity: 1; transform: translateY(0);
  transition: opacity 0.5s ease, transform 0.5s ease, border-color 0.3s ease, background 0.3s ease;
  transition-delay: calc(0.1s + var(--i) * 0.08s);
}
.goal-card:hover {
  border-color: rgba(99,102,241,0.2);
  background: #101024;
}
.goal-number {
  font-size: 11px; font-weight: 700; color: var(--accent);
  margin-bottom: 12px; letter-spacing: 2px; opacity: 0.7;
}
.goal-card h3 {
  font-size: 16px; font-weight: 600; color: #d4d4e4; margin-bottom: 8px;
}
.goal-card p {
  font-size: 14px; color: #8a8aa8; line-height: 1.75; margin: 0;
}

/* ── CTA Section ──────────────────────────────── */
.cta-section {
  position: relative; z-index: 1;
  display: flex; justify-content: center; padding: 0 24px 100px;
  opacity: 0; transform: translateY(24px);
  transition: opacity 0.7s cubic-bezier(0.22, 0.61, 0.36, 1),
              transform 0.7s cubic-bezier(0.22, 0.61, 0.36, 1);
}
.cta-section.revealed { opacity: 1; transform: translateY(0); }
.cta-inner {
  display: flex; flex-direction: column; align-items: center;
  text-align: center; max-width: 560px;
}
.cta-quote {
  font-family: Georgia, 'Times New Roman', 'Noto Serif SC', serif;
  font-size: 18px; font-style: italic; color: var(--text-secondary);
  margin: 0 0 14px; line-height: 1.6;
}
.cta-text {
  font-size: 15px; color: var(--text-muted); line-height: 1.8;
  margin: 0 0 32px;
}

/* ── Footer ───────────────────────────────────── */
.welcome-footer {
  position: relative; z-index: 1;
  text-align: center; padding: 28px 24px 36px; margin-top: auto;
  border-top: 1px solid rgba(255,255,255,0.04);
}
.welcome-footer p {
  font-size: 12px; color: var(--text-muted); margin: 0; letter-spacing: 0.5px;
}

/* ── Responsive ───────────────────────────────── */
@media (max-width: 720px) {
  .hero-title { font-size: 38px; }
  .hero-glow { width: 400px; height: 300px; }
  .hero-actions { flex-direction: column; }
  .hero-section { padding: 100px 20px 60px; }
  .tribute-card { padding: 32px 24px; }
  .tribute-quote-open, .tribute-quote-close { font-size: 40px; }
  .tribute-quote-text { font-size: 19px; }
  .vision-grid { grid-template-columns: 1fr; }
  .goals-grid { grid-template-columns: 1fr; }
  .content-section { padding-bottom: 64px; }
}

@media (max-width: 480px) {
  .hero-title { font-size: 32px; }
  .hero-subtitle { font-size: 11px; letter-spacing: 2px; }
  .hero-tagline { font-size: 17px; }
  .hero-desc { font-size: 14px; }
  .hero-section { padding: 100px 16px 48px; }
  .hero-actions { gap: 10px; width: 100%; }
  .btn-hero-primary, .btn-hero-secondary { width: 100%; padding: 13px 24px; font-size: 15px; }
  .tribute-card { padding: 24px 18px; }
  .tribute-quote-open, .tribute-quote-close { font-size: 34px; }
  .tribute-quote-text { font-size: 17px; }
  .tribute-body { font-size: 14px; }
  .tribute-links { gap: 10px; }
  .tribute-link { padding: 6px 14px; font-size: 12px; }
  .vision-card { padding: 22px 20px; }
  .vision-card p { font-size: 13px; }
  .eor-card { padding: 18px 20px; }
  .eor-card-text p { font-size: 13px; }
  .eor-conclusion { font-size: 14px; padding: 16px 18px; }
  .goal-card { padding: 22px 20px; }
  .goal-card p { font-size: 13px; }
  .cta-inner { max-width: 100%; }
  .cta-quote { font-size: 16px; }
  .cta-text { font-size: 14px; }
  .content-section { padding: 0 16px 56px; }
  .welcome-topbar { padding: 0 10px; }
  .lang-select { font-size: 12px; padding: 4px 8px; }
  .topbar-brand { font-size: 14px; }
}

@media (min-width: 1600px) {
  .content-section { max-width: 1300px; }
  .hero-desc { max-width: 800px; }
  .hero-tagline { max-width: 800px; }
}
</style>
