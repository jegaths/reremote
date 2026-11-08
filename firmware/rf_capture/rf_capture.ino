/*
 * rf_capture.ino  --  raw 433 MHz OOK frame capture
 *                     for Arduino Nano (ATmega328P) and ESP32 boards
 *                     (ESP32, ESP32-S2, ESP32-S3, ESP32-C3, ESP32-C6)
 *
 * Wiring, Arduino Nano:
 *   receiver VCC  -> Nano 5V
 *   receiver GND  -> Nano GND
 *   receiver DATA -> Nano D2   (INT0, external interrupt)
 *
 * Wiring, any ESP32 (3.3 V logic: power the receiver from 3V3, NOT 5V/VBUS,
 * otherwise its DATA line drives 5 V into a GPIO that is not 5 V tolerant):
 *   receiver VCC  -> 3V3
 *   receiver GND  -> GND
 *   receiver DATA -> GPIO 4    (change RX_PIN below if you prefer another pin)
 *
 * Both, optional: antenna -> 17.3 cm straight wire on the ANT pad (quarter wave @ 433.92 MHz)
 *
 * Boards with native USB (S2/S3/C3/C6) often have two USB sockets. Built with
 * "USB CDC On Boot: Enabled", this sketch mirrors its output to both the
 * native USB port ("USB") and the UART bridge ("COM"/"UART"), so either socket
 * works. A classic ESP32 has only the UART bridge and uses plain Serial.
 *
 * What it does
 *   Every edge on D2 is timestamped in an interrupt. The main loop groups the
 *   edge-to-edge durations into frames: a frame ends whenever a single duration
 *   exceeds the "separation gap" (default 4000 us), which is how every common
 *   remote marks the silence between repeats. Each frame is streamed over the
 *   serial port as plain text so the host decides what the protocol is.
 *
 * Serial output (115200 baud), one line per frame:
 *   F <millis> <n> <d1> <d2> ... <dn>
 *     d1 is the first HIGH pulse after the separation gap, the durations then
 *     alternate HIGH/LOW, and dn is the terminating LOW gap itself.
 *   I rf_capture <version> gap=<us> min_pulse=<us> min_edges=<n>
 *     identification line, printed at boot and on request.
 *   E <message>   error / diagnostic
 *
 * Serial commands (newline terminated):
 *   ?             print the I line
 *   G <us>        set separation gap, e.g. "G 3000"
 *   P <us>        set minimum pulse width accepted inside a frame
 *   N <n>         set minimum number of edges for a frame to be reported
 */

#define FW_VERSION "1.2.0"

// ---- board selection ---------------------------------------------------------
#if defined(ARDUINO_ARCH_ESP32)
 #if CONFIG_IDF_TARGET_ESP32S3
  #define BOARD_NAME "esp32s3"
 #elif CONFIG_IDF_TARGET_ESP32S2
  #define BOARD_NAME "esp32s2"
 #elif CONFIG_IDF_TARGET_ESP32C3
  #define BOARD_NAME "esp32c3"
 #elif CONFIG_IDF_TARGET_ESP32C6
  #define BOARD_NAME "esp32c6"
 #else
  #define BOARD_NAME "esp32"
 #endif
  #define RX_PIN 4
  #define ISR_ATTR IRAM_ATTR
 #if ARDUINO_USB_CDC_ON_BOOT
  // Native USB chip: Serial is the USB port, Serial0 the UART bridge. Mirror to both.
  struct DualSerial : public Print {
    size_t write(uint8_t c) override { Serial.write(c); Serial0.write(c); return 1; }
    size_t write(const uint8_t *b, size_t n) override { Serial.write(b, n); Serial0.write(b, n); return n; }
    int available() { return Serial.available() + Serial0.available(); }
    int read() { return Serial.available() ? Serial.read() : Serial0.read(); }
    void begin(unsigned long baud) {
      Serial.begin(baud);
      Serial0.begin(baud);
#if ARDUINO_USB_MODE
      Serial.setTxTimeoutMs(0);   // never block when no host is on the native port
#endif
    }
  } OUT;
 #else
  #define OUT Serial
 #endif
#else
  #define BOARD_NAME "nano"
  #define RX_PIN 2
  #define ISR_ATTR
  #define OUT Serial
#endif

// Tunables (runtime adjustable through serial commands)
static uint16_t sepGapUs     = 4000;  // a duration longer than this ends a frame
static uint16_t minPulseUs   = 80;    // shorter pulses mean the frame is noise
static uint8_t  minEdges     = 16;    // fewer edges than this is not a remote frame

// Ring buffer written by the ISR, drained by loop()
#define RING_SIZE 256                 // must be a power of two
static volatile uint16_t ring[RING_SIZE];
static volatile uint8_t ringHead = 0; // written by ISR
static uint8_t ringTail = 0;          // read by loop
static volatile uint8_t overflows = 0;

// Frame under construction
#define MAX_EDGES 240
static uint16_t frame[MAX_EDGES];
static uint16_t frameLen = 0;
static bool     frameValid = true;    // false once a too-short pulse was seen
static bool     synced = false;       // true after the first separation gap

static void ISR_ATTR onEdge() {
  static unsigned long lastUs = 0;
  unsigned long now = micros();
  unsigned long d = now - lastUs;
  lastUs = now;
  if (d > 65535UL) d = 65535UL;
  uint8_t next = (ringHead + 1) & (RING_SIZE - 1);
  if (next == ringTail) {             // buffer full, drop the edge
    overflows++;
    return;
  }
  ring[ringHead] = (uint16_t)d;
  ringHead = next;
}

static void printIdent() {
  OUT.print(F("I rf_capture "));
  OUT.print(F(FW_VERSION));
  OUT.print(F(" board="));
  OUT.print(F(BOARD_NAME));
  OUT.print(F(" pin="));
  OUT.print(RX_PIN);
  OUT.print(F(" gap="));
  OUT.print(sepGapUs);
  OUT.print(F(" min_pulse="));
  OUT.print(minPulseUs);
  OUT.print(F(" min_edges="));
  OUT.println(minEdges);
}

static void emitFrame(uint16_t gap) {
  // Frame must look like [H L H L ... H] + gap, so an odd edge count before
  // the gap. Allow even too, the host can cope, but reject tiny frames.
  OUT.print(F("F "));
  OUT.print(millis());
  OUT.print(' ');
  OUT.print(frameLen + 1);
  for (uint16_t i = 0; i < frameLen; i++) {
    OUT.print(' ');
    OUT.print(frame[i]);
  }
  OUT.print(' ');
  OUT.println(gap);
}

static void resetFrame() {
  frameLen = 0;
  frameValid = true;
}

static void handleDuration(uint16_t d) {
  if (d >= sepGapUs) {
    // End of frame (or just silence). Report if it looks like a real burst.
    if (synced && frameValid && frameLen >= minEdges && frameLen < MAX_EDGES) {
      emitFrame(d);
    }
    synced = true;
    resetFrame();
    return;
  }
  if (!synced) return;                // ignore everything before the first gap
  if (d < minPulseUs) frameValid = false;
  if (frameLen < MAX_EDGES) {
    frame[frameLen++] = d;
  } else {
    frameValid = false;               // runaway noise, wait for next gap
  }
}

static void handleCommand(char *line) {
  while (*line == ' ') line++;
  char cmd = line[0];
  long val = atol(line + 1);
  switch (cmd) {
    case '?': printIdent(); break;
    case 'G': if (val >= 500 && val <= 60000) sepGapUs = (uint16_t)val; printIdent(); break;
    case 'P': if (val >= 10 && val <= 5000)  minPulseUs = (uint16_t)val; printIdent(); break;
    case 'N': if (val >= 4 && val <= 200)    minEdges = (uint8_t)val; printIdent(); break;
    case 0: break;
    default:
      OUT.print(F("E unknown command: "));
      OUT.println(line);
  }
}

void setup() {
  OUT.begin(115200);
  pinMode(RX_PIN, INPUT);
  attachInterrupt(digitalPinToInterrupt(RX_PIN), onEdge, CHANGE);
  printIdent();
}

void loop() {
  // Drain the ring buffer
  while (ringTail != ringHead) {
    uint16_t d = ring[ringTail];
    ringTail = (ringTail + 1) & (RING_SIZE - 1);
    handleDuration(d);
  }

  if (overflows) {
    uint8_t n = overflows;
    overflows = 0;
    OUT.print(F("E ring overflow x"));
    OUT.println(n);
    resetFrame();
    synced = false;
  }

  // Serial command parser
  static char buf[24];
  static uint8_t len = 0;
  while (OUT.available()) {
    char c = (char)OUT.read();
    if (c == '\n' || c == '\r') {
      buf[len] = 0;
      if (len) handleCommand(buf);
      len = 0;
    } else if (len < sizeof(buf) - 1) {
      buf[len++] = c;
    }
  }
}
