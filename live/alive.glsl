// ytm alive: the living layer under the player (a ghostty custom shader, runs on the gpu).
// ytm rewrites the three lines marked @palette and @state on every track change, then sends its ghostty window SIGUSR2
// (ghostty reloads config + shaders on that signal). the cover's colours become slow light drifting behind the text:
// calm when paused, flowing when playing. text stays crisp: light only lands where the frame is dark.

const vec3 C1 = vec3(0.278, 0.702, 0.847);  // @palette main
const vec3 C2 = vec3(0.545, 0.424, 1.000);  // @palette second
const vec3 C3 = vec3(0.910, 0.561, 0.706);  // @palette spark
const float PLAYING = 1.0;                  // @state 1 playing · 0 paused
const float ENERGY = 0.6;                   // @state 0 calm (work mode) → 1 lively

float hash(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }

float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
}

float fbm(vec2 p) {
    float v = 0.0, a = 0.5;
    for (int i = 0; i < 4; i++) { v += a * noise(p); p = p * 2.03 + vec2(1.7, 9.2); a *= 0.5; }
    return v;
}

// soft drifting motes, like dust in a projector beam
float motes(vec2 uv, float t) {
    float m = 0.0;
    for (int i = 0; i < 3; i++) {
        float fi = float(i);
        vec2 g = uv * (6.0 + fi * 3.0) + vec2(t * (0.6 + fi * 0.3), -t * (0.25 + fi * 0.1));
        vec2 id = floor(g), f = fract(g) - 0.5;
        float h = hash(id + fi * 17.0);
        vec2 o = vec2(hash(id + 3.1), hash(id + 7.7)) - 0.5;
        float d = length(f - o * 0.7);
        float tw = 0.5 + 0.5 * sin(t * 2.0 + h * 40.0);
        m += step(0.86, h) * smoothstep(0.09, 0.0, d) * tw * (0.6 - fi * 0.15);
    }
    return m;
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 uv = fragCoord / iResolution.xy;
    vec4 term = texture(iChannel0, uv);
    float speed = mix(0.015, 0.06 + 0.04 * ENERGY, PLAYING);
    float t = iTime * speed;
    vec2 p = uv * vec2(iResolution.x / iResolution.y, 1.0) * 1.4;

    // two layers of slow flow: the colour field, then a warp so it never looks like a gradient
    float w = fbm(p * 1.3 - vec2(t * 0.8, t * 0.3));
    float n = fbm(p + vec2(t, -t * 0.6) + 1.6 * w);
    vec3 light = mix(C1, C2, smoothstep(0.25, 0.75, n));
    light = mix(light, C3, smoothstep(0.62, 0.92, fbm(p * 0.7 + vec2(-t * 0.4, t * 0.9))));

    // breathing: a slow swell while playing (about 70 bpm), still when paused
    float breath = 0.5 + 0.5 * sin(iTime * 1.15);
    float amount = smoothstep(0.3, 1.0, n) * (0.16 + 0.07 * ENERGY * PLAYING * breath);
    float dust = motes(uv * vec2(iResolution.x / iResolution.y, 1.0), iTime * mix(0.05, 0.22, PLAYING)) * (0.10 + 0.08 * PLAYING);

    // keep text crisp: light only where the frame is dark
    float lum = dot(term.rgb, vec3(0.299, 0.587, 0.114));
    float mask = 1.0 - smoothstep(0.16, 0.42, lum);
    vec2 v = uv - 0.5;
    float vignette = 1.0 - dot(v, v) * 0.9;

    vec3 col = term.rgb + (light * amount + mix(C3, vec3(1.0), 0.5) * dust) * mask;
    col *= mix(1.0, vignette, 0.55);
    col += (hash(fragCoord + fract(iTime) * 91.0) - 0.5) * 0.012;   // film grain, barely there
    fragColor = vec4(col, term.a);
}
