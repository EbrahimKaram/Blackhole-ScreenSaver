#version 100
#ifdef GL_ES
precision highp float;
#endif
// star-gaze — Gargantua-style raytraced black hole (Schwarzschild approx).
// Technique follows "Raytracing a Black Hole with WebGPU"
// (threejsroadmap.com, Dan Greenheck): geodesic raymarch + analytic thin-disk
// hits + blackbody disk + D^3 doppler beaming + keplerian turbulence,
// with the bent ray sampling a procedural starfield (lensing comes free).
//
// NOTE: animation time is decoded from the input frame (appsrc time-code,
// written by star-gaze.py), NOT from the glshader `time` uniform, which is
// unix-epoch based and unusable for smooth animation.
varying vec2 v_texcoord;
uniform sampler2D tex;
uniform float time;
uniform float width;
uniform float height;

#define DIN 3.0     // disk inner radius (dramatic, inside ISCO like the blog)
#define DOUT 12.0   // disk outer radius
#define RS 2.0      // Schwarzschild radius (M = 1, geometric units)
#define STEPS 64

// ---------- time + mouse steering from host ----------
// Frame rows 0..3 are full-row uniform colors: time-code, yaw, pitch,
// influence. (glshader `time` is epoch-based; these rows are our own clock.)
float sceneTime() {
  vec2 res = vec2(width, height);
  vec4 tc = texture2D(tex, vec2(0.5, 0.5 / res.y));
  return dot(tc.rgb * 255.0, vec3(1.0, 256.0, 65536.0)) / 256.0;
}
vec3 mouseSteer() {
  vec2 res = vec2(width, height);
  float yaw = (texture2D(tex, vec2(0.5, 1.5 / res.y)).r * 255.0 / 255.0 - 0.5) * 1.4;
  float pitch = (texture2D(tex, vec2(0.5, 2.5 / res.y)).r * 255.0 / 255.0 - 0.5) * 0.64;
  float inf = texture2D(tex, vec2(0.5, 3.5 / res.y)).r;
  return vec3(yaw, pitch, inf);
}

// ---------- hashes / noise ----------
float hash21(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}
vec2 hash22(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973));
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.xx + p3.yz) * p3.zy);
}
float hash31(vec3 p) {
  return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453);
}
float noise3(vec3 p) {
  vec3 i = floor(p);
  vec3 f = fract(p);
  vec3 u = f * f * (3.0 - 2.0 * f);
  float a = hash31(i);
  float b = hash31(i + vec3(1.0, 0.0, 0.0));
  float c = hash31(i + vec3(0.0, 1.0, 0.0));
  float d = hash31(i + vec3(1.0, 1.0, 0.0));
  float e = hash31(i + vec3(0.0, 0.0, 1.0));
  float f2 = hash31(i + vec3(1.0, 0.0, 1.0));
  float g = hash31(i + vec3(0.0, 1.0, 1.0));
  float h = hash31(i + vec3(1.0, 1.0, 1.0));
  return mix(mix(mix(a, b, u.x), mix(c, d, u.x), u.y),
             mix(mix(e, f2, u.x), mix(g, h, u.x), u.y), u.z);
}
float fbm3(vec3 p, float lac, float pers) {
  float v = 0.0;
  float a = 0.5;
  for (int i = 0; i < 3; i++) {
    v += a * noise3(p);
    p *= lac;
    a *= pers;
  }
  return v;
}

// ---------- background: lensed starfield + nebula ----------
vec3 starLayer(vec3 d) {
  float theta = atan(d.z, d.x);
  float phi = asin(clamp(d.y, -1.0, 1.0));
  vec2 sc = vec2(theta, phi) * 60.0;
  vec2 cell = floor(sc);
  vec2 cuv = fract(sc);
  float h = hash21(cell);
  if (h > 0.14) return vec3(0.0);
  vec2 sp = hash22(cell + 42.0) * 0.8 + 0.1;
  float dist = length(cuv - sp);
  float sz = 0.012 + 0.030 * hash21(cell + 100.0);
  float core = smoothstep(sz, 0.0, dist);
  float glow = smoothstep(sz * 3.0, 0.0, dist) * 0.30;
  vec3 tint = mix(vec3(0.80, 0.90, 1.0), vec3(1.0, 0.95, 0.80), hash21(cell + 200.0));
  return tint * (core + glow) * 2.0;
}
vec3 nebula(vec3 d) {
  float n1 = fbm3(d * 3.1, 2.03, 0.5);
  float n2 = fbm3(d * 6.7 + 7.3, 2.03, 0.5);
  vec3 c = vec3(0.10, 0.16, 0.38) * pow(smoothstep(0.38, 0.92, n1), 1.5) * 0.55;
  c += vec3(0.32, 0.09, 0.30) * pow(smoothstep(0.46, 0.95, n2), 1.5) * 0.45;
  return c;
}

// ---------- blackbody palette (exact port of the demo's Charity fit) ----------
vec3 blackbody(float tk) {
  float x = clamp((tk - 1000.0) / 9000.0, 0.0, 1.0);
  float r = clamp(1.0 - (x - 0.8) * 2.0, 0.5, 1.0);
  float g = smoothstep(0.0, 0.5, x) * (1.0 - max(x - 0.7, 0.0) * 0.3);
  float b = smoothstep(0.3, 1.0, x) * x;
  return vec3(r, g, b);
}

// ---------- accretion disk (ported tuning from the demo) ----------
#define ROT_SPEED -5.0
#define ROT_SIGN -1.0
float turbSample(float hr, float ang, float phase) {
  float ra = ang + phase;
  vec3 nc = vec3(hr * 1.81, cos(ra) / 0.75, sin(ra) / 0.75);
  return fbm3(nc, 2.5, 0.8);
}
vec4 diskColor(float hr, vec3 hit, vec3 rayDir, float t) {
  float normR = clamp((hr - DIN) / (DOUT - DIN), 0.0, 1.0);
  // demo edge softness: 0.18 inner / 0.5 outer
  float edge = smoothstep(0.0, 0.18, normR) * smoothstep(1.0, 0.5, normR);
  // demo temperature: steep falloff to a 1500K rim, ~45kK peak
  float tempK = mix(1500.0, 45000.0, pow(DIN / hr, 5.0));
  vec3 col = blackbody(tempK);
  // keplerian rotation with cyclic crossfade (no infinite wind-up)
  float ang = atan(hit.z, hit.x);
  float cyc = 8.0;
  float ct = mod(t, cyc);
  float blend = ct / cyc;
  float k1 = ct * ROT_SPEED / pow(hr, 1.5);
  float k2 = (ct + cyc) * ROT_SPEED / pow(hr, 1.5);
  float tb1 = turbSample(hr, ang, k1);
  float tb2 = turbSample(hr, ang, k2);
  float tb = mix(tb2, tb1, blend);
  float rings = pow(clamp(tb, 0.0, 1.0), 6.5);
  float alpha = edge * (0.15 + 0.85 * rings);
  // doppler beaming: D^3, keplerian beta (demo formula)
  vec3 vel = vec3(-sin(ang) * ROT_SIGN, 0.0, cos(ang) * ROT_SIGN);
  float beta = (1.0 / sqrt(hr / DIN)) * 0.3;
  float D = 1.0 / max(1.0 - beta * dot(vel, rayDir), 1e-3);
  float boost = clamp(pow(D, 3.0), 0.1, 5.0);
  col *= boost * (0.32 + 0.68 * rings);
  col *= 3.5; // demo diskBrightness (tempered for our filmic path)
  return vec4(col, alpha);
}

// ---------- main raymarcher ----------
void main() {
  float t = sceneTime();
  vec2 res = vec2(width, height);
  vec2 fuv = vec2(v_texcoord.x, 1.0 - v_texcoord.y);
  vec2 suv = (fuv - 0.5) * 2.0;
  suv.x *= res.x / res.y;

  // cinematic flight: full orbit ~52s, plus slow push-in/pull-out and
  // inclination sweeps (near edge-on <-> tilted) on longer periods
  vec3 steer = mouseSteer();
  float camA = 0.6 + t * 0.10 + steer.x * steer.z;
  float camR = 21.0 + sin(t * 0.045) * 2.5;
  float camE = clamp(0.32 + 0.55 * sin(t * 0.05 + 3.25) + steer.y * steer.z, -1.2, 1.4);
  vec3 camPos = vec3(cos(camA) * cos(camE) * camR, sin(camE) * camR,
                     sin(camA) * cos(camE) * camR);
  vec3 fwd = normalize(vec3(0.0) - camPos);
  vec3 right = normalize(cross(vec3(0.0, 1.0, 0.0), fwd));
  vec3 up = cross(fwd, right);
  vec3 rayDir = normalize(fwd * 1.5 + right * suv.x + up * suv.y);

  vec3 pos = camPos;
  vec3 prev = camPos;
  vec3 col = vec3(0.0);
  float alpha = 0.0;
  bool captured = false;
  bool escaped = false;
  for (int i = 0; i < STEPS; i++) {
    float r = length(pos);
    if (r < RS * 1.01) { captured = true; break; }
    if (r > 50.0) { escaped = true; break; }
    float stepLen = 0.12 + r * 0.09;
    vec3 toC = -pos / max(r, 1e-3);
    rayDir = normalize(rayDir + toC * (RS / (r * r)) * stepLen * 2.2);
    prev = pos;
    pos = pos + rayDir * stepLen;
    if (prev.y * pos.y < 0.0) {
      float f = prev.y / (prev.y - pos.y);
      vec3 hit = mix(prev, pos, f);
      float hr = length(hit.xz);
      if (hr > DIN && hr < DOUT) {
        vec4 dk = diskColor(hr, hit, rayDir, t);
        float rem = 1.0 - alpha;
        col += dk.rgb * dk.a * rem;
        alpha += rem * dk.a;
        if (alpha > 0.99) break;
      }
    }
  }
  if (!captured) {
    vec3 rd = normalize(rayDir);
    vec3 bg = vec3(0.004, 0.005, 0.012);
    bg += starLayer(rd);
    bg += nebula(rd);
    col = col + bg * (1.0 - alpha);
  }

  col = vec3(1.0) - exp(-col * 1.7);
  // keep the fire saturated (filmic desaturates); then gentle vignette+grain
  float luma = dot(col, vec3(0.299, 0.587, 0.114));
  col = mix(vec3(luma), col, 1.45);
  vec2 q = fuv - 0.5;
  col *= 1.0 - dot(q, q) * 0.55;
  col += (hash21(fuv * res + fract(time) * 127.0) - 0.5) * 0.014;
  gl_FragColor = vec4(col, 1.0);
}
