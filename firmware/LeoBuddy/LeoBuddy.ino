/*
 * Leo Buddy: SSD1306 animated face using U8g2 full-frame refresh.
 * Target: ESP32 Dev Module / ESP32-WROOM-32.
 * OLED: SSD1306 128x64, address 0x3C (0x3D fallback), SDA 21, SCL 22.
 * Servos: pan GPIO18, tilt GPIO19, constrained to 85..95 degrees.
 * Switch: GPIO23 to GND, INPUT_PULLUP. USB serial protocol: 115200 baud.
 *
 * Derived and adapted for Leo Buddy from the eye-animation project
 * "ESP32-Eyes" by Alastair Aitchison / Playful Technology:
 * https://github.com/playfultechnology/esp32-eyes
 * Upstream copyright: Copyright (c) 2023 Alastair Aitchison, Playful Technology.
 * Modified for Leo Buddy in 2026: adapted eye-animation behavior plus the
 * SSD1306/U8g2 renderer, five states, switch, servo control, and Pi serial link.
 * This modified firmware is distributed under GPL-3.0-only; see LICENSE in
 * this directory. U8g2 is a separate required library dependency.
 * SPDX-License-Identifier: GPL-3.0-only
 */
#include <Arduino.h>
#include <Wire.h>
#include <U8g2lib.h>
#include <math.h>
#include <string.h>

namespace Leo {
constexpr uint8_t SDA_PIN = 21, SCL_PIN = 22;
constexpr uint8_t OLED_ADDRS[] = {0x3C, 0x3D};
constexpr uint8_t SWITCH_PIN = 23, PAN_PIN = 18, TILT_PIN = 19;
constexpr uint8_t PAN_CHANNEL = 0, TILT_CHANNEL = 1;
constexpr uint32_t SERIAL_BAUD = 115200;
constexpr uint32_t I2C_HZ = 400000;
constexpr uint32_t FRAME_MS = 34; // 29.4 fps; full 1 KiB SSD1306 transfer
constexpr uint32_t DEBOUNCE_MS = 35, SERVO_STEP_MS = 20, SPEAK_POSE_MS = 360;
constexpr int SERVO_MIN = 85, SERVO_NEUTRAL = 90, SERVO_MAX = 95;
constexpr uint32_t BLINK_MIN_MS = 2200, BLINK_MAX_MS = 4300;
constexpr uint32_t BLINK_CLOSE_MS = 100, BLINK_HOLD_MS = 170, BLINK_OPEN_MS = 170;
constexpr uint8_t SERVO_BITS = 16;
constexpr size_t RX_CAPACITY = 48;
}

// Hardware constructor uses the confirmed ESP32 I2C pins. U8g2's full
// framebuffer avoids the custom chunk cursor and restart behavior in the old
// renderer; each sendBuffer() transmits one complete, consistent frame.
U8G2_SSD1306_128X64_NONAME_F_HW_I2C oled(U8G2_R0, U8X8_PIN_NONE,
                                          Leo::SCL_PIN, Leo::SDA_PIN);

enum class State : uint8_t { Idle, Listening, Thinking, Speaking, Error };
struct Axis { uint8_t pin, channel; int angle, target; bool ready; };
State state = State::Idle;
const char *stateText(State s) {
  switch (s) {
    case State::Idle: return "IDLE";
    case State::Listening: return "LISTENING";
    case State::Thinking: return "THINKING";
    case State::Speaking: return "SPEAKING";
    case State::Error: return "ERROR";
  }
  return "IDLE";
}

Axis pan{Leo::PAN_PIN, Leo::PAN_CHANNEL, 90, 90, false};
Axis tilt{Leo::TILT_PIN, Leo::TILT_CHANNEL, 90, 90, false};

uint8_t oledAddress = 0x3C;
bool oledReady = false;
bool rawSwitchHigh = true, stableSwitchHigh = true;
uint32_t rawChangedAt = 0, nextFrameAt = 0, lastServoAt = 0;
uint32_t nextBlinkAt = 0, blinkStartedAt = 0, nextPoseAt = 0;
uint32_t blinkCount = 0, displayFrameCount = 0, lastReportAt = 0;
bool blinkActive = false;
uint8_t speakPose = 0, thinkPose = 0;
char rxLine[Leo::RX_CAPACITY] = {};
size_t rxLength = 0;
bool droppingLine = false, frameDirty = true;

float ease(float t) {
  t = constrain(t, 0.0f, 1.0f);
  return t * t * (3.0f - 2.0f * t);
}

void scheduleBlink(uint32_t now) {
  nextBlinkAt = now + random(Leo::BLINK_MIN_MS, Leo::BLINK_MAX_MS + 1);
}

float blinkAmount(uint32_t now) {
  if (!blinkActive && static_cast<int32_t>(now - nextBlinkAt) >= 0) {
    blinkActive = true;
    blinkStartedAt = now;
    ++blinkCount;
  }
  if (!blinkActive) return 0.0f;
  const uint32_t elapsed = now - blinkStartedAt;
  if (elapsed < Leo::BLINK_CLOSE_MS)
    return ease(static_cast<float>(elapsed) / Leo::BLINK_CLOSE_MS);
  if (elapsed < Leo::BLINK_CLOSE_MS + Leo::BLINK_HOLD_MS) return 1.0f;
  const uint32_t reopenAt = Leo::BLINK_CLOSE_MS + Leo::BLINK_HOLD_MS;
  if (elapsed < reopenAt + Leo::BLINK_OPEN_MS)
    return 1.0f - ease(static_cast<float>(elapsed - reopenAt) / Leo::BLINK_OPEN_MS);
  blinkActive = false;
  scheduleBlink(now);
  return 0.0f;
}

void drawEye(int x, int centerY, int width, int height, int pupilDx, int pupilDy,
             float blink, bool angry, bool left) {
  const int baseHeight = height;
  height = max(2, static_cast<int>(baseHeight * (1.0f - blink * 0.96f)));
  const int y = centerY - height / 2;
  if (height <= 4) {
    // A crisp closed-lid stroke is visible even on the shortest blink frame.
    oled.drawHLine(x + 2, centerY, width - 4);
    if (height >= 3) oled.drawHLine(x + 4, centerY + 1, width - 8);
  } else {
    oled.drawRBox(x, y, width, height, min(9, height / 2));
    const int pupilW = max(5, width / 5);
    const int pupilH = max(5, height / 2);
    const int px = constrain(x + width / 2 - pupilW / 2 + pupilDx,
                             x + 3, x + width - pupilW - 3);
    const int py = constrain(y + height / 2 - pupilH / 2 + pupilDy,
                             y + 2, y + height - pupilH - 2);
    oled.setDrawColor(0);
    oled.drawRBox(px, py, pupilW, pupilH, min(3, pupilH / 2));
    oled.setDrawColor(1);
  }
  if (angry) {
    const int browY = centerY - baseHeight / 2 - 4;
    if (left) oled.drawLine(x + 3, browY, x + width - 2, browY + 4);
    else oled.drawLine(x + 2, browY + 4, x + width - 3, browY);
  }
}

void drawFace(uint32_t now) {
  const float blink = blinkAmount(now); // serviced autonomously every frame
  float openness = 1.0f, gazeX = 0.0f, gazeY = 0.0f;
  bool angry = false;
  switch (state) {
    case State::Idle:
      gazeX = sinf(now / 2700.0f) * 2.0f;
      gazeY = sinf(now / 3400.0f) * 1.0f;
      break;
    case State::Listening:
      openness = 1.08f;
      gazeX = sinf(now / 800.0f) * 2.0f;
      gazeY = -1.0f;
      break;
    case State::Thinking:
      openness = 0.76f;
      gazeX = 4.0f + sinf(now / 650.0f) * 1.5f;
      gazeY = -2.0f;
      break;
    case State::Speaking:
      openness = 0.98f;
      gazeX = sinf(now / 540.0f) * 2.0f;
      gazeY = sinf(now / 700.0f);
      break;
    case State::Error:
      openness = 0.72f;
      gazeX = -2.0f;
      gazeY = 3.0f;
      angry = true;
      break;
  }
  float talkPulse = state == State::Speaking ? sinf(now / 105.0f) * 2.0f : 0.0f;
  const int height = max(9, static_cast<int>(28.0f * openness));
  const int leftH = state == State::Speaking ? height + static_cast<int>(talkPulse) : height;
  const int rightH = state == State::Speaking ? height - static_cast<int>(talkPulse * 0.6f) : height;

  oled.clearBuffer();
  drawEye(17, 31, 38, leftH, static_cast<int>(gazeX), static_cast<int>(gazeY), blink, angry, true);
  drawEye(73, 31, 38, rightH, static_cast<int>(gazeX), static_cast<int>(gazeY), blink, angry, false);

  // Speaking has a small animated mouth; other states retain the two-eye style.
  if (state == State::Speaking) {
    const int mouthH = 2 + static_cast<int>((sinf(now / 90.0f) + 1.0f) * 3.0f);
    oled.drawRFrame(59, 54 - mouthH / 2, 10, mouthH + 3, 2);
  }
  oled.sendBuffer();
  ++displayFrameCount;
  frameDirty = false;
}

void setPose(int panAngle, int tiltAngle, uint32_t now) {
  pan.target = constrain(panAngle, Leo::SERVO_MIN, Leo::SERVO_MAX);
  tilt.target = constrain(tiltAngle, Leo::SERVO_MIN, Leo::SERVO_MAX);
  nextPoseAt = now + Leo::SPEAK_POSE_MS;
}

void setState(State next) {
  if (next == state) return;
  state = next;
  const uint32_t now = millis();
  switch (state) {
    case State::Idle: setPose(90, 90, now); break;
    case State::Listening: setPose(90, 95, now); break;
    case State::Thinking: thinkPose = 0; setPose(95, 85, now); break;
    case State::Speaking: speakPose = 0; setPose(85, 90, now); break;
    case State::Error: setPose(85, 85, now); break;
  }
  frameDirty = true;
  Serial.printf("LEO:STATE:%s:PAN=%d:TILT=%d\n", stateText(state), pan.target, tilt.target);
}

void writeAxis(const Axis &axis) {
  if (!axis.ready) return;
  const int angle = constrain(axis.angle, Leo::SERVO_MIN, Leo::SERVO_MAX);
  const uint32_t pulseUs = 1500 + (angle - Leo::SERVO_NEUTRAL) * 10;
  const uint32_t duty = (static_cast<uint64_t>(pulseUs) * 65535ULL) / 20000ULL;
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(axis.pin, duty);
#else
  ledcWrite(axis.channel, duty);
#endif
}

bool attachAxis(Axis &axis) {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  axis.ready = ledcAttach(axis.pin, 50, Leo::SERVO_BITS);
#else
  axis.ready = ledcSetup(axis.channel, 50, Leo::SERVO_BITS) > 0;
  if (axis.ready) ledcAttachPin(axis.pin, axis.channel);
#endif
  return axis.ready;
}

void updateServos(uint32_t now) {
  if (now - lastServoAt < Leo::SERVO_STEP_MS) return;
  lastServoAt = now;
  if (state == State::Speaking && static_cast<int32_t>(now - nextPoseAt) >= 0) {
    static const int panPos[] = {85, 95, 95, 85};
    static const int tiltPos[] = {90, 85, 95, 90};
    setPose(panPos[speakPose], tiltPos[speakPose], now);
    speakPose = (speakPose + 1) % 4;
  } else if (state == State::Thinking && static_cast<int32_t>(now - nextPoseAt) >= 0) {
    thinkPose ^= 1;
    setPose(thinkPose ? 95 : 92, 85, now);
  }
  Axis *axes[] = {&pan, &tilt};
  for (Axis *axis : axes) {
    if (axis->angle < axis->target) ++axis->angle;
    else if (axis->angle > axis->target) --axis->angle;
    writeAxis(*axis);
  }
}

void setStateFromLine(char *line) {
  while (*line == ' ' || *line == '\t') ++line;
  size_t n = strlen(line);
  while (n && (line[n - 1] == '\r' || line[n - 1] == ' ' || line[n - 1] == '\t')) line[--n] = 0;
  for (size_t i = 0; i < n; ++i) if (line[i] >= 'a' && line[i] <= 'z') line[i] -= 32;
  if (!strcmp(line, "IDLE")) setState(State::Idle);
  else if (!strcmp(line, "LISTENING")) setState(State::Listening);
  else if (!strcmp(line, "THINKING")) setState(State::Thinking);
  else if (!strcmp(line, "SPEAKING")) setState(State::Speaking);
  else if (!strcmp(line, "ERROR")) setState(State::Error);
  else if (!strcmp(line, "SYNC")) Serial.printf("SWITCH:STATE:%s\n", stableSwitchHigh ? "HIGH" : "LOW");
  else if (!strcmp(line, "PING")) Serial.println("PONG");
  else if (!strcmp(line, "STATUS")) Serial.println("ESP32_UART0_READY");
  else if (n) Serial.println("LEO:ERR:UNKNOWN_COMMAND");
}

void pollSerial() {
  for (uint8_t budget = 0; budget < 48 && Serial.available() > 0; ++budget) {
    const char c = static_cast<char>(Serial.read());
    if (c == '\r') continue;
    if (c == '\n') {
      if (!droppingLine) { rxLine[rxLength] = 0; setStateFromLine(rxLine); }
      else Serial.println("LEO:ERR:LINE_TOO_LONG");
      rxLength = 0;
      droppingLine = false;
    } else if (!droppingLine) {
      if (rxLength + 1 >= sizeof(rxLine)) { rxLength = 0; droppingLine = true; }
      else rxLine[rxLength++] = c;
    }
  }
}

void pollSwitch(uint32_t now) {
  const bool level = digitalRead(Leo::SWITCH_PIN) == HIGH;
  if (level != rawSwitchHigh) { rawSwitchHigh = level; rawChangedAt = now; }
  if (rawSwitchHigh == stableSwitchHigh || now - rawChangedAt < Leo::DEBOUNCE_MS) return;
  const bool oldLevel = stableSwitchHigh;
  stableSwitchHigh = rawSwitchHigh;
  const char *edge = oldLevel ? "HIGH_TO_LOW" : "LOW_TO_HIGH";
  Serial.printf("LEO:SWITCH_EDGE:%s\n", edge);
  if (state == State::Idle) {
    setState(State::Listening);
    Serial.printf("SWITCH:EDGE:%s\n", edge);
    Serial.println("LEO:SWITCH_ACCEPTED:IDLE");
  } else {
    Serial.printf("LEO:SWITCH_IGNORED:ACTIVE:%s\n", stateText(state));
    Serial.printf("SWITCH:EDGE:%s\n", edge);
  }
}

bool detectOled(uint8_t &address) {
  for (uint8_t candidate : Leo::OLED_ADDRS) {
    Wire.beginTransmission(candidate);
    if (Wire.endTransmission() == 0) { address = candidate; return true; }
  }
  return false;
}

void setup() {
  Serial.begin(Leo::SERIAL_BAUD);
  pinMode(Leo::SWITCH_PIN, INPUT_PULLUP);
  stableSwitchHigh = rawSwitchHigh = digitalRead(Leo::SWITCH_PIN) == HIGH;
  rawChangedAt = millis();
  Wire.begin(Leo::SDA_PIN, Leo::SCL_PIN, Leo::I2C_HZ);
  Wire.setTimeOut(20);
  randomSeed(esp_random());
  // A first blink occurs shortly after boot so a bench check is quick; later
  // intervals are randomized to the normal relaxed 2.2..4.3 second range.
  nextBlinkAt = millis() + 900;
  uint8_t found = 0;
  if (detectOled(found)) {
    oledAddress = found;
    oled.setI2CAddress(static_cast<uint8_t>(oledAddress << 1));
    oled.setBusClock(Leo::I2C_HZ);
    oledReady = oled.begin();
  }
  Serial.printf("LEO:OLED:%s:SSD1306:0x%02X:SDA21:SCL22:U8g2-full-buffer\n",
                oledReady ? "READY" : "NOT_FOUND", oledAddress);
  if (oledReady) { drawFace(millis()); nextFrameAt = millis() + Leo::FRAME_MS; }

  const bool panOk = attachAxis(pan);
  const bool tiltOk = attachAxis(tilt);
  writeAxis(pan);
  writeAxis(tilt);
  Serial.printf("LEO:SERVO:GPIO18:%s:GPIO19:%s:limits=85..95\n",
                panOk ? "OK" : "FAILED", tiltOk ? "OK" : "FAILED");
  Serial.printf("LEO:READY:IDLE:USB=%lu:SWITCH_INITIAL=%s:NO_SESSION\n",
                static_cast<unsigned long>(Leo::SERIAL_BAUD), stableSwitchHigh ? "HIGH" : "LOW");
}

void loop() {
  pollSerial();
  const uint32_t now = millis();
  pollSwitch(now);
  updateServos(now);
  if (oledReady && (frameDirty || static_cast<int32_t>(now - nextFrameAt) >= 0)) {
    drawFace(now);
    nextFrameAt = now + Leo::FRAME_MS;
  }
  if (now - lastReportAt >= 10000) {
    lastReportAt = now;
    // Compact diagnostics stay safely below the Pi parser's line bound.
    Serial.printf("LEO:ANIM:frames=%lu:blink_events=%lu:%s:%s\n",
                  static_cast<unsigned long>(displayFrameCount),
                  static_cast<unsigned long>(blinkCount),
                  blinkActive ? "CLOSING_OR_OPENING" : "OPEN",
                  stateText(state));
  }
  delay(0);
}
