// avr8js setups per chip, shared by the browser (sim/simulator.js) and the headless verifier
// (backend/verify/verify.mjs). This file imports nothing: each caller passes in its own avr8js module.
//
// avr8js ships configs for the ATmega328P (Uno, Nano). For the ATmega2560 (Mega) we build them here from:
//   - interrupt vector numbers in avr-libc's iomxx0_1.h (avr8js stores vector number x 2),
//   - register addresses in the same header (I/O registers + 0x20 = data address),
//   - output-compare pins from the Arduino core's variants/mega/pins_arduino.h.

// Intel HEX loader: data records (00) plus extended address records (02, 04) for programs over 64 KB.
export function loadHex(source, target) {
  let base = 0;
  for (const line of source.split('\n')) {
    if (line[0] !== ':') continue;
    const n = parseInt(line.substr(1, 2), 16);
    const addr = parseInt(line.substr(3, 4), 16);
    const type = line.substr(7, 2);
    if (type === '00') {
      for (let i = 0; i < n; i++) target[base + addr + i] = parseInt(line.substr(9 + i * 2, 2), 16);
    } else if (type === '02') {
      base = parseInt(line.substr(9, 4), 16) << 4;
    } else if (type === '04') {
      base = parseInt(line.substr(9, 4), 16) << 16;
    }
  }
}

const vec = (n) => n * 2; // avr8js interrupt address = vector number x 2

function atmega2560(avr) {
  const PORT = (letter) => avr[`port${letter}Config`].PORT;
  const noInterrupts = (cfg) => ({ ...cfg, externalInterrupts: [], pinChange: undefined });

  // Timers 0-2: same registers as the 328P, different vectors and compare-output pins.
  const timer0 = { ...avr.timer0Config, compAInterrupt: vec(21), compBInterrupt: vec(22), ovfInterrupt: vec(23),
    compPortA: PORT('B'), compPinA: 7, compPortB: PORT('G'), compPinB: 5, externalClockPort: PORT('D'), externalClockPin: 7 };
  const timer1 = { ...avr.timer1Config, captureInterrupt: vec(16), compAInterrupt: vec(17), compBInterrupt: vec(18),
    compCInterrupt: vec(19), ovfInterrupt: vec(20), OCRC: 0x8c, OCFC: 0x08, OCIEC: 0x08,
    compPortA: PORT('B'), compPinA: 5, compPortB: PORT('B'), compPinB: 6, compPortC: PORT('B'), compPinC: 7,
    externalClockPort: PORT('D'), externalClockPin: 6 };
  const timer2 = { ...avr.timer2Config, compAInterrupt: vec(13), compBInterrupt: vec(14), ovfInterrupt: vec(15),
    compPortA: PORT('B'), compPinA: 4, compPortB: PORT('H'), compPinB: 6, externalClockPort: 0, externalClockPin: 0 };

  // Timers 3-5: 16-bit like timer1, with their own registers, vectors and pins.
  const timer16 = (r, v, port, pins, clockPort, clockPin) => ({
    ...avr.timer1Config,
    TIFR: r.TIFR, TCCRA: r.base, TCCRB: r.base + 1, TCCRC: r.base + 2, TCNT: r.base + 4, ICR: r.base + 6,
    OCRA: r.base + 8, OCRB: r.base + 10, OCRC: r.base + 12, TIMSK: r.TIMSK, OCFC: 0x08, OCIEC: 0x08,
    captureInterrupt: vec(v), compAInterrupt: vec(v + 1), compBInterrupt: vec(v + 2), compCInterrupt: vec(v + 3), ovfInterrupt: vec(v + 4),
    compPortA: PORT(port), compPinA: pins[0], compPortB: PORT(port), compPinB: pins[1], compPortC: PORT(port), compPinC: pins[2],
    externalClockPort: PORT(clockPort), externalClockPin: clockPin,
  });
  const timer3 = timer16({ base: 0x90, TIFR: 0x38, TIMSK: 0x71 }, 31, 'E', [3, 4, 5], 'E', 6);
  const timer4 = timer16({ base: 0xa0, TIFR: 0x39, TIMSK: 0x72 }, 41, 'H', [3, 4, 5], 'H', 7);
  const timer5 = timer16({ base: 0x120, TIFR: 0x3a, TIMSK: 0x73 }, 46, 'L', [3, 4, 5], 'L', 2);

  // External interrupts: INT0-3 on PD0-3 (EICRA 0x69), INT4-7 on PE4-7 (EICRB 0x6a); EIMSK 0x3d, EIFR 0x3c.
  const ext = (eicr, index, iscOffset) => ({ EICR: eicr, EIMSK: 0x3d, EIFR: 0x3c, index, iscOffset, interrupt: vec(index + 1) });
  const ports = {};
  for (const letter of 'ABCDEFGHJKL') ports[letter] = noInterrupts(avr[`port${letter}Config`]);
  ports.B = { ...ports.B, pinChange: { ...avr.portBConfig.pinChange, pinChangeInterrupt: vec(9) } }; // PCINT0-7
  ports.D = { ...ports.D, externalInterrupts: [ext(0x69, 0, 0), ext(0x69, 1, 2), ext(0x69, 2, 4), ext(0x69, 3, 6)] };
  ports.E = { ...ports.E, externalInterrupts: [null, null, null, null, ext(0x6a, 4, 0), ext(0x6a, 5, 2), ext(0x6a, 6, 4), ext(0x6a, 7, 6)] };
  ports.K = { ...ports.K, pinChange: { PCIE: 2, PCICR: 0x68, PCIFR: 0x3b, PCMSK: 0x6d, pinChangeInterrupt: vec(11), mask: 0xff, offset: 0 } }; // PCINT16-23

  // ADC: 16 channels; A8-A15 are selected with the MUX5 bit (avr8js adds 0x20 to the channel number).
  const single = (channel) => ({ type: 0, channel });
  const muxChannels = { 0x1e: { type: 2, voltage: 1.1 }, 0x1f: { type: 2, voltage: 0 } };
  for (let i = 0; i < 8; i++) { muxChannels[i] = single(i); muxChannels[0x20 + i] = single(8 + i); }
  const adc = { ...avr.adcConfig, numChannels: 16, muxInputMask: 0x3f, muxChannels };

  return {
    progBytes: 256 * 1024, // over 128 KB, so avr8js uses the 2560's 3-byte return addresses
    sramBytes: 0x2200 - 0x100, // data space up to RAMEND 0x21FF (registers, I/O, extended I/O, 8 KB SRAM)
    timers: [timer0, timer1, timer2, timer3, timer4, timer5],
    ports,
    adc,
  };
}

function atmega328p(avr) {
  return {
    progBytes: 32 * 1024,
    sramBytes: 8192, // avr8js default (more than the real 2 KB; harmless)
    timers: [avr.timer0Config, avr.timer1Config, avr.timer2Config],
    ports: { B: avr.portBConfig, C: avr.portCConfig, D: avr.portDConfig },
    adc: avr.adcConfig,
  };
}

// mcu: "atmega328p" (Uno, Nano) or "atmega2560" (Mega). avr: the avr8js module.
export function chipSetup(avr, mcu) {
  if (mcu === 'atmega2560') return atmega2560(avr);
  return atmega328p(avr);
}
