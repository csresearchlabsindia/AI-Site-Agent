// ASA Mission Control v3 - STM32 LED matrix: ASA brand + hard hat with tick/cross
#include <Arduino_RouterBridge.h>
#include <Arduino_LED_Matrix.h>

#define BRAND_TICKER 0   // 0 = "ASA" + scanner bar | 1 = scrolling "ASA-01  SITE SAFETY"
#define ICONS_SIDE   0   // 0 = big hat alternating with big tick/cross | 1 = mini hat beside mini tick/cross

// LISTEN animation: 1 = talking face, 2 = voice waveform, 0 = equaliser
#define LISTEN_STYLE 1
// IDLE screen: 1 = face, 0 = ASA brand
#define IDLE_FACE 1
#include <math.h>
Arduino_LED_Matrix matrix;
uint8_t frame[104];
enum { S_IDLE=0, S_VIOLATION=1, S_OFFLINE=2, S_THANKS=3, S_BOOT=4, S_COMPLIANT=5, S_CHECKING=6, S_NOT_ON_HEAD=7, S_LISTEN=8, S_THINK=9, S_ATTENTION=10 };
volatile int steady = S_BOOT;
volatile unsigned long last_msg = 0, thanks_start = 0;
volatile bool thanks_on = false;
volatile int oneshot = S_THANKS;

const char* const HELMET[8] = {".............",".....*+*.....","...***+***...","..****+****..","..****+****..","..****+****..","#############","............."};
const char* const HOLLOW[8] = {".............",".....###.....","...##...##...","..#.......#..","..#.......#..","..#.......#..","#############","............."};
const char* const TICK[8]   = {"..........#..",".........##..","........##...",".......##....","..#...##.....","..##.##......","...###.......","....#........"};
const char* const CROSS[8]  = {"..##.....##..","...##...##...","....##.##....",".....###.....",".....###.....","....##.##....","...##...##...","..##.....##.."};
const char* const ARROW[8]  = {"......#......",".....###.....","....#####....","...##.#.##...","......#......","......#......","......#......","......#......"};
const char* const ASA[8]    = {".............","..#..###..#..",".#.#.#...#.#.",".###.###.###.",".#.#...#.#.#.",".#.#.###.#.#.",".............","............."};
const char* const MINI_OK[8]  = {".............",".............","..###.......#",".#####.....#.",".#####..#.#..","#######..#...",".............","............."};
const char* const MINI_BAD[8] = {".............","........#...#","..###....#.#.",".#...#....#..",".#...#...#.#.","#######.#...#",".............","............."};
const char* const SPK_MUTE[8] = {".............","....##.......","...###.#...#.",".#####..#.#..",".#####...#...",".#####..#.#..","...###.#...#.","....##......."};
const char* const QMARK[8]  = {".....###.....","....#...#....","........#....",".......#.....","......#......","......#......",".............","......#......"};

struct Glyph { char ch; const char* rows[5]; };
const Glyph FONT[] = {
  {'A', {".#.","#.#","###","#.#","#.#"}}, {'S', {"###","#..","###","..#","###"}},
  {'-', {"...","...","###","...","..."}}, {'0', {"###","#.#","#.#","#.#","###"}},
  {'1', {".#.","##.",".#.",".#.","###"}}, {'I', {"###",".#.",".#.",".#.","###"}},
  {'T', {"###",".#.",".#.",".#.",".#."}}, {'E', {"###","#..","##.","#..","###"}},
  {'F', {"###","#..","##.","#..","#.."}}, {'Y', {"#.#","#.#",".#.",".#.",".#."}},
  {' ', {"...","...","...","...","..."}}
};
const char* TICKER = "ASA-01  SITE SAFETY   ";

uint8_t lv(char ch) { switch (ch) { case '+': return 3; case '*': return 5; case '#': return 7; default: return 0; } }
void px(int r, int c, uint8_t v) { if (r >= 0 && r < 8 && c >= 0 && c < 13) frame[r * 13 + c] = v; }
void drawIcon(const char* const ic[8], int scale = 7, int shift = 0, int maxCol = 13) {
  for (int r = 0; r < 8; r++) for (int c = 0; c < maxCol; c++) {
    uint8_t v = lv(ic[r][c]); if (!v) continue;
    int s = v * scale / 7; if (s > 7) s = 7; if (s < 1) s = 1;
    px((r - shift + 8) % 8, c, s);
  }
}
void shine(unsigned long k) {
  if (k >= 650) return;
  int x = k / 50;
  for (int r = 1; r <= 5; r++) for (int c = x - 1; c <= x; c++) if (c >= 0 && c < 13 && frame[r * 13 + c]) frame[r * 13 + c] = 7;
}
void scanner(unsigned long t) {
  unsigned long k = t % 2400; int p = (k < 1200 ? k : 2400 - k) * 10 / 1200;
  for (int c = p - 1; c <= p + 3; c++) px(7, c, (c >= p && c <= p + 2) ? 7 : 2);
}
const Glyph* glyph(char ch) { for (auto &g : FONT) if (g.ch == ch) return &g; return &FONT[10]; }
void ticker(unsigned long t, uint8_t v) {
  int W = strlen(TICKER) * 4, off = (t / 90) % (W + 13);
  for (int c = 0; c < 13; c++) {
    int x = c + off - 13; if (x < 0 || x >= W || x % 4 == 3) continue;
    const Glyph* g = glyph(TICKER[x / 4]);
    for (int r = 0; r < 5; r++) if (g->rows[r][x % 4] == '#') px(r + 1, c, v);
  }
}
void sparkle(unsigned long t) {
  uint32_t x = (t / 90 + 1) * 2654435761UL;
  for (int i = 0; i < 4; i++) { x ^= x << 13; x ^= x >> 17; x ^= x << 5; int r = x % 8, c = (x >> 8) % 13; if (!frame[r * 13 + c]) frame[r * 13 + c] = 3; }
}
void hatOk(unsigned long k) {
#if ICONS_SIDE
  drawIcon(MINI_OK);
#else
  drawIcon(HELMET); shine(k);
#endif
}

static uint32_t hsh(uint32_t n) {
  n = (n ^ 0x9e3779b9u) * 0x85ebca6bu; n ^= n >> 13; n *= 0xc2b2ae35u; return n ^ (n >> 16);
}
static void lpx(int r, int c, int v) {
  if (r < 0 || r > 7 || c < 0 || c > 12) return;
  if (v > 7) v = 7; if (v < 0) v = 0;
  px(r, c, v);
}
// Talking face: eyes blink every 3.2 s, mouth opens 0-3 rows, changing every 110 ms
static void listenFace(unsigned long t) {
  bool blink = (t % 3200) < 160;
  for (int c0 = 2; c0 <= 9; c0 += 7)
    for (int c = c0; c < c0 + 2; c++) { if (!blink) lpx(1, c, 6); lpx(2, c, 6); }
  int o = hsh(t / 110) % 4;
  int top = 5 - o / 2, bot = 5 + (o + 1) / 2;
  for (int r = top; r <= bot; r++)
    for (int c = 4; c <= 8; c++) {
      bool re = (r == top || r == bot), ce = (c == 4 || c == 8);
      if (re && ce && bot > top) continue;          // rounded corners
      lpx(r, c, (re || ce) ? 7 : 2);
    }
}
// Voice waveform: scrolling trace with a speech-like envelope, anti-aliased across 2 rows
static void listenWave(unsigned long t) {
  float tf = (float)(t % 600000UL);                 // keep float precision on long uptimes
  for (int c = 0; c < 13; c++) {
    float a = 0.5f + 2.9f * fabsf(sinf(tf / 230.0f + c * 0.35f) * sinf(tf / 370.0f - c * 0.2f));
    float y = 3.5f + a * sinf(c * 0.9f - tf / 90.0f);
    int fy = (int)floorf(y); float fr = y - fy;
    lpx(fy, c, (int)(7 * (1 - fr) + 1));
    lpx(fy + 1, c, (int)(7 * fr + 1));
  }
}

// Thinking (silent synthesis gap): eyes glance, mouth closed, dots pulse
static void listenThink(unsigned long t) {
#if LISTEN_STYLE == 2
  for (int c = 0; c < 13; c++) lpx(4, c, 2);
  lpx(4, (t / 90) % 13, 7);
#else
  static const int8_t GL[4] = {0, 1, 0, -1};
  int g = GL[(t / 700) % 4];
  bool blink = (t % 3200) < 160;
  for (int c0 = 2; c0 <= 9; c0 += 7)
    for (int c = c0; c < c0 + 2; c++) { if (!blink) lpx(1, c + g, 6); lpx(2, c + g, 6); }
  for (int c = 5; c <= 7; c++) lpx(5, c, 4);
  int d = (t / 300) % 3;
  for (int k = 0; k < 3; k++) lpx(7, 5 + k, k == d ? 7 : 1);
#endif
}

// Idle face: calm smile, eyes scan the site left/right, blink every ~4 s, smile glows
static void idleFace(unsigned long t) {
  static const int8_t GL[8] = {0, 0, -1, -1, 0, 0, 1, 1};
  int g = GL[(t / 800) % 8];
  bool blink = (t % 4100) < 150;
  for (int c0 = 2; c0 <= 9; c0 += 7)
    for (int c = c0; c < c0 + 2; c++) { if (!blink) lpx(1, c + g, 6); lpx(2, c + g, 6); }
  int ph = (t / 120) % 20;
  int glow = 3 + (ph < 10 ? ph : 20 - ph) / 4;      // 3..5 breathing
  lpx(5, 3, glow); lpx(5, 9, glow);
  for (int c = 4; c <= 8; c++) lpx(6, c, glow);
}

const int L_RPWM=3,L_LPWM=5,R_RPWM=6,R_LPWM=9,EN_ALL=4;
const bool L_INVERT=false,R_INVERT=false;
const int PWM_MAX=200;
const unsigned long DEADMAN_MS=400,RAMP_MS=250;
volatile int tgtL=0,tgtR=0; volatile unsigned long last_drive=0; volatile bool armed=false;
int curL=0,curR=0; unsigned long last_tick=0;
static void applySide(int rp,int lp,int v,bool inv){ if(inv)v=-v;
  if(v>=0){analogWrite(lp,0);analogWrite(rp,v);} else {analogWrite(rp,0);analogWrite(lp,-v);} }
static void driveOff(){ curL=curR=0;tgtL=tgtR=0;armed=false;
  analogWrite(L_RPWM,0);analogWrite(L_LPWM,0);analogWrite(R_RPWM,0);analogWrite(R_LPWM,0);
  digitalWrite(EN_ALL,LOW); }
bool drive(int l,int r){ if(l>PWM_MAX)l=PWM_MAX; if(l<-PWM_MAX)l=-PWM_MAX;
  if(r>PWM_MAX)r=PWM_MAX; if(r<-PWM_MAX)r=-PWM_MAX;
  tgtL=l;tgtR=r;last_drive=millis();armed=true; return true; }
bool halt(){ tgtL=0;tgtR=0;armed=false; return true; }
static int rampTo(int c,int t,int s){ if(t>c)return (t-c>s)?c+s:t; if(t<c)return (c-t>s)?c-s:t; return c; }
static void driveTick(unsigned long t){ unsigned long dt=t-last_tick; if(dt<10)return; last_tick=t;
  if(armed&&t-last_drive>DEADMAN_MS){driveOff();return;}
  if(!armed){ if(curL||curR)driveOff(); return; }
  int st=(int)((dt*PWM_MAX)/RAMP_MS); if(st<1)st=1;
  curL=rampTo(curL,tgtL,st); curR=rampTo(curR,tgtR,st);
  digitalWrite(EN_ALL,HIGH);
  applySide(L_RPWM,L_LPWM,curL,L_INVERT); applySide(R_RPWM,R_LPWM,curR,R_INVERT); }

void set_status(int s) {
  last_msg = millis();
  if (s == S_THANKS) { oneshot = s; thanks_start = millis(); thanks_on = true; return; }
  if (s == S_VIOLATION || s == S_NOT_ON_HEAD || s == S_LISTEN || s == S_THINK) thanks_on = false;
  steady = s;
}

void setup() {
  pinMode(EN_ALL, OUTPUT);
  driveOff();
  matrix.begin();
  matrix.setGrayscaleBits(3);
  matrix.clear();
  Bridge.begin();
  Bridge.provide("set_status", set_status);
  Bridge.provide("drive", drive);
  Bridge.provide("halt", halt);
}

void loop() {
  memset(frame, 0, sizeof(frame));
  unsigned long t = millis();
  driveTick(t);
  int s = steady;
  if (thanks_on && t - thanks_start > (oneshot == S_LISTEN ? 2500UL : 3000UL)) thanks_on = false;
  if (t - last_msg > 5000) s = -1;                 // watchdog: app silent -> "?"
  else if (thanks_on) s = oneshot;
  switch (s) {
    case S_IDLE:
#if IDLE_FACE
      idleFace(t); break;
#else
#if BRAND_TICKER
      ticker(t, 5);
#else
      drawIcon(ASA, 5);
#endif
      scanner(t); break;
#endif
    case S_CHECKING: { drawIcon(ASA); uint8_t v = (t / 150) % 2 ? 3 : 7; for (int c = 0; c < 13; c++) px(7, c, v); break; }
    case S_COMPLIANT: {
#if ICONS_SIDE
      drawIcon(MINI_OK);
#else
      unsigned long k = t % 2000; if (k < 1200) { drawIcon(HELMET); shine(k); } else drawIcon(TICK);
#endif
      break; }
    case S_VIOLATION: {
#if ICONS_SIDE
      if (t % 600 < 400) drawIcon(MINI_BAD);
#else
      unsigned long k = t % 1000; if (k < 400) drawIcon(HOLLOW); else if (k >= 500 && k < 900) drawIcon(CROSS);
#endif
      break; }
    case S_NOT_ON_HEAD: {
      unsigned long k = t % 2000;
      if (k < 1000) drawIcon(ARROW, 7, (k / 100) % 8);
#if ICONS_SIDE
      else drawIcon(MINI_BAD);
#else
      else drawIcon(k < 1500 ? HOLLOW : CROSS);
#endif
      break; }
    case S_THANKS: {
      unsigned long e = t - thanks_start;
      if (e < 1800) { int n = e / 40 + 1; if (n > 13) n = 13; drawIcon(TICK, 7, 0, n); if (e > 500) sparkle(t); }
      else hatOk(e - 1800);
      break; }
    case S_OFFLINE: {
      unsigned long k = t % 2000, tri = k < 1000 ? k : 2000 - k;
      drawIcon(ASA, 1 + 4 * tri / 1000);
      for (int c = 0; c < 13; c += 2) px(7, c, 2);
      break; }
    case S_BOOT: {
      unsigned long k = t % 2400; int cols = k < 300 ? 0 : k < 600 ? 4 : k < 900 ? 8 : 13;
      drawIcon(ASA, 7, 0, cols);
      int p = k / 150; if (p > 13) p = 13; for (int c = 0; c < p; c++) px(7, c, 4);
      break; }
    case S_LISTEN:
#if LISTEN_STYLE == 1
      listenFace(t); break;
#elif LISTEN_STYLE == 2
      listenWave(t); break;
#else
    {
      uint32_t x = (t / 80 + 7) * 2654435761UL;
      for (int c = 0; c <= 6; c++) {
        x ^= x << 13; x ^= x >> 17; x ^= x << 5;
        int h = 1 + c * 5 / 6 + (int)(x % 3); if (h > 8) h = 8;
        int top = (8 - h) / 2; uint8_t v = 3 + c * 4 / 6;
        for (int r = top; r < top + h; r++) { px(r, c, v); px(r, 12 - c, v); }
      }
      break; }
#endif
    case S_ATTENTION: {
      unsigned long k = t % 3000;
      if (k < 2000) {
#if IDLE_FACE
        idleFace(t);
#else
        drawIcon(ASA, 5); scanner(t);
#endif
      } else {
        drawIcon(SPK_MUTE, (k % 500 < 250) ? 7 : 3);
      }
      break; }
    case S_THINK: listenThink(t); break;
    default: if ((t / 500) % 2 == 0) drawIcon(QMARK); break;
  }
  matrix.draw(frame);
  delay(20);
}
