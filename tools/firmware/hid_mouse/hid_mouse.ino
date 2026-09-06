/*
 * Прошивка "миша по команді" для effortless.mouse.SerialHidTransport.
 *
 * Плата з нативним USB (ATmega32u4: Leonardo, Pro Micro; або RP2040 з ядром
 * arduino-pico) вмикається в комп'ютер і представляється звичайною USB-мишею.
 * Python шле сюди команди через віртуальний COM-порт, плата перетворює їх на
 * HID-звіти.
 *
 * Чому це інший поверх, ніж SendInput: для системи тут немає жодної
 * "згенерованої" події. Є USB-пристрій класу HID, який шле звіти — рівно як
 * будь-яка інша миша. Прапорця LLMHF_INJECTED немає не тому, що його вдалося
 * приховати, а тому, що позначати нічого: подія не проходила через шар, який
 * цей прапорець ставить.
 *
 * ВАЖЛИВО: плата має бути з нативним USB. Arduino Uno/Nano (ATmega328p) не
 * підійде — у них USB реалізований окремою мікросхемою і бібліотеки Mouse
 * для них немає.
 *
 * Протокол — кадри рівно по 3 байти (команда + два байти даних):
 *
 *     'M' dx dy      зміщення, по int8 на вісь
 *     'B' mask 0     стан кнопок: біт0 ліва, біт1 права, біт2 середня
 *     'W' clicks 0   колесо
 *     'H' clicks 0   горизонтальне колесо (див. примітку нижче)
 *
 * Фіксована довжина навмисно: текстовий протокол на 1000 Гц не встигає —
 * рядок у десяток символів їде близько мілісекунди, а це і є весь такт.
 *
 * Швидкість порту для нативного USB значення не має (CDC ігнорує baudrate),
 * тому в Python можна лишати будь-яку.
 */

#include <Mouse.h>

const uint8_t FRAME_SIZE = 3;

// Порядок відповідає бітам маски в transport.py: біт0 ліва, біт1 права,
// біт2 середня.
const uint8_t BUTTON_BITS[3] = {0x01, 0x02, 0x04};
const uint8_t BUTTON_CODES[3] = {MOUSE_LEFT, MOUSE_RIGHT, MOUSE_MIDDLE};

uint8_t buttonState = 0;

void setup() {
  Serial.begin(500000);
  Mouse.begin();
}

// Стан кнопок приходить маскою, а не подіями "натиснув"/"відпустив":
// так host і плата не можуть розійтися, і кнопка не залишиться затиснутою,
// якщо якийсь кадр загубиться.
void applyButtons(uint8_t mask) {
  for (uint8_t i = 0; i < 3; i++) {
    bool wanted = mask & BUTTON_BITS[i];
    bool active = buttonState & BUTTON_BITS[i];
    if (wanted && !active) {
      Mouse.press(BUTTON_CODES[i]);
    } else if (!wanted && active) {
      Mouse.release(BUTTON_CODES[i]);
    }
  }
  buttonState = mask;
}

void loop() {
  if (Serial.available() < FRAME_SIZE) {
    return;
  }

  uint8_t command = Serial.read();
  int8_t a = (int8_t)Serial.read();
  int8_t b = (int8_t)Serial.read();

  switch (command) {
    case 'M':
      Mouse.move(a, b, 0);
      break;
    case 'B':
      applyButtons((uint8_t)a);
      break;
    case 'W':
      Mouse.move(0, 0, a);
      break;
    case 'H':
      // Стандартна бібліотека Mouse не має осі AC Pan — горизонтальне
      // колесо тут просто ігнорується. Щоб воно запрацювало, потрібен
      // власний HID-дескриптор; для наведення й кліків це не потрібно.
      break;
    default:
      // Розсинхронізувались на межі кадру. Найпростіше й найнадійніше —
      // скинути буфер і чекати наступного цілого кадру.
      while (Serial.available()) {
        Serial.read();
      }
      break;
  }
}
