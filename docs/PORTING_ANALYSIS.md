# NOM 1 J2ME analysis

Reference JAR analyzed for the first Android port:

- Size: 129,210 bytes
- SHA-256: bd92119dfdb2e31655f0ba9fe6f82a8bbb88c3cf0b9792b426ab9ddee6d995b2
- MIDlet-Name: NOM (by GDC)
- MIDlet-Version: 1.0.25
- MIDlet-Vendor: Living Mobile
- Main MIDlet class: Nom1
- MIDP 1.0 / CLDC 1.0

## Input

The main game canvas class overrides keyPressed(int). The bytecode preserves standard numeric J2ME key codes before falling back to Canvas.getGameAction(), so the Android gesture adapter deliberately injects the original numeric codes instead of Android key codes.

| Android gesture | J2ME code | Phone key |
| --- | ---: | --- |
| Tap | 53 | 5 / FIRE / OK |
| Swipe up | 50 | 2 |
| Swipe left | 52 | 4 |
| Swipe right | 54 | 6 |
| Swipe down | 56 | 8 |

## Display

The JAR contains several 176px-wide visual assets, including the Living Mobile logo. The game bytecode also contains screen-width handling around 144/176 pixel layouts, indicating a multi-screen J2ME build.

The first Android profile therefore uses a 176x208 virtual canvas and lets J2ME Loader scale it to fit modern Galaxy displays while preserving the original aspect ratio.

## Audio/resources

The JAR includes MIDI and WAV resources plus PNG and proprietary .pzx data. Keeping the original JAR intact inside the APK allows the J2ME compatibility runtime to preserve these resources instead of manually converting them.
