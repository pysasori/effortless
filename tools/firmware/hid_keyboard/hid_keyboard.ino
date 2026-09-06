/*
 * Прошивка "клавіатура по команді" для effortless.keyboard.SerialHidTransport.
 *
 * Плата з нативним USB (ATmega32u4: Leonardo, Pro Micro; або RP2040 з ядром
 * arduino-pico) вмикається в комп'ютер і представляється звичайною USB-
 * клавіатурою. Python шле сюди готові HID-звіти через віртуальний COM-порт,
 * плата віддає їх системі як є.
 *
 * Чому це інший поверх, ніж SendInput: для системи тут немає жодної
 * "згенерованої" події. Є USB-пристрій класу HID, який шле звіти — рівно як
 * будь-яка інша клавіатура. Прапорця LLKHF_INJECTED немає не тому, що його
 * вдалося приховати, а тому, що позначати нічого: подія не проходила через
 * шар, який цей прапорець ставить. З тієї ж причини тут неможливий і
 * VK_PACKET: плата шле позицію клавіші, а символ обчислює драйвер за
 * розкладкою — так само, як для живої клавіатури.
 *
 * ВАЖЛИВО: плата має бути з нативним USB. Arduino Uno/Nano (ATmega328p) не
 * підійде — у них USB реалізований окремою мікросхемою і бібліотеки Keyboard
 * для них немає.
 *
 * Протокол — кадри рівно по 9 байтів:
 *
 *     'K' mods reserved k1 k2 k3 k4 k5 k6
 *
 * Це байт команди плюс стандартний 8-байтовий звіт boot-протоколу:
 * маска модифікаторів, зарезервований байт і до шести HID Usage ID
 * одночасно натиснутих клавіш (0 — порожньо).
 *
 * Використано `HID-Project` (бібліотека NicoHood) заради `BootKeyboard`:
 * стандартна `Keyboard.h` уміє лише press/release за ASCII і не дає
 * скласти довільний звіт, а нам потрібен саме повний стан — інакше
 * накладання клавіш передати неможливо.
 *
 * Стан, а не події: якщо кадр загубиться, наступний однаково несе повну
 * картину, і клавіша не лишиться затиснутою назавжди.
 *
 * Швидкість порту для нативного USB значення не має (CDC ігнорує baudrate),
 * тому в Python можна лишати будь-яку.
 */

#include <HID-Project.h>

const uint8_t FRAME_SIZE = 9;   // 'K' + 8 байтів звіту
const uint8_t REPORT_SIZE = 8;

// Модифікатори в HID-звіті живуть не в списку клавіш, а в окремому байті.
// Порядок бітів той самий, що в transport.py.
const uint8_t MODIFIER_BITS[8] = {0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80};
const uint8_t MODIFIER_USAGES[8] = {
  0xE0, 0xE1, 0xE2, 0xE3,  // лівий Ctrl, Shift, Alt, Gui
  0xE4, 0xE5, 0xE6, 0xE7   // правий Ctrl, Shift, Alt, Gui
};

uint8_t report[REPORT_SIZE];
uint8_t previous[REPORT_SIZE];

void setup() {
  Serial.begin(500000);
  BootKeyboard.begin();
  memset(previous, 0, REPORT_SIZE);
}

// Різниця між минулим і новим станом перетворюється на press/release.
// Саме різниця, а не сам звіт: BootKeyboard тримає власний буфер, і
// перезаписувати його напряму бібліотека не дає.
void applyReport(const uint8_t *next) {
  uint8_t modifiers = next[0];
  uint8_t wasModifiers = previous[0];

  for (uint8_t i = 0; i < 8; i++) {
    bool wanted = modifiers & MODIFIER_BITS[i];
    bool active = wasModifiers & MODIFIER_BITS[i];
    if (wanted && !active) {
      BootKeyboard.press(KeyboardKeycode(MODIFIER_USAGES[i]));
    } else if (!wanted && active) {
      BootKeyboard.release(KeyboardKeycode(MODIFIER_USAGES[i]));
    }
  }

  // Відпускання першими: якщо в одному звіті одна клавіша пішла вгору, а
  // інша вниз, такий порядок не створює миті, коли натиснуті обидві.
  for (uint8_t i = 2; i < REPORT_SIZE; i++) {
    uint8_t key = previous[i];
    if (key == 0) continue;
    bool stillDown = false;
    for (uint8_t j = 2; j < REPORT_SIZE; j++) {
      if (next[j] == key) { stillDown = true; break; }
    }
    if (!stillDown) {
      BootKeyboard.release(KeyboardKeycode(key));
    }
  }

  for (uint8_t i = 2; i < REPORT_SIZE; i++) {
    uint8_t key = next[i];
    if (key == 0) continue;
    bool wasDown = false;
    for (uint8_t j = 2; j < REPORT_SIZE; j++) {
      if (previous[j] == key) { wasDown = true; break; }
    }
    if (!wasDown) {
      BootKeyboard.press(KeyboardKeycode(key));
    }
  }

  memcpy(previous, next, REPORT_SIZE);
}

void loop() {
  if (Serial.available() < FRAME_SIZE) {
    return;
  }

  uint8_t command = Serial.read();
  if (command != 'K') {
    // Розсинхронізувались на межі кадру. Найпростіше й найнадійніше —
    // скинути буфер і чекати наступного цілого кадру.
    while (Serial.available()) {
      Serial.read();
    }
    return;
  }

  for (uint8_t i = 0; i < REPORT_SIZE; i++) {
    report[i] = Serial.read();
  }
  applyReport(report);
}
