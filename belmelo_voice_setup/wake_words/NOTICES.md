# Sources and notices — 15 November 2026

The Belmelo model weights were trained from scratch, then fine-tuned on synthetic
wake-plus-command examples. They are supplied under Apache License 2.0 in
LICENSE.model.txt. This does not change the licences of source software or data.

- microWakeWord: OHF-Voice/micro-wake-word, Apache-2.0, commit
  4665173cd35f1cff9a61e06fc427f124766c488e.
  https://github.com/OHF-Voice/micro-wake-word
- Piper sample generator: rhasspy/piper-sample-generator, MIT, commit
  2971426a55072f7d22fec416ca7800df8bd23207.
  https://github.com/rhasspy/piper-sample-generator
- Synthetic voice checkpoint: en_US-libritts_r-medium.pt, published v2.0.0 by
  rhasspy/piper-sample-generator, SHA-256
  e95ee53770bf598c354a6e6dbfc95ccb259aeeb501d35a86be8a767429ab0ff6.
  Source model card: https://huggingface.co/datasets/rhasspy/piper-checkpoints/blob/main/en/en_US/libritts_r/medium/MODEL_CARD
- LibriTTS-R, Yip et al., 2023; source corpus declared CC BY 4.0:
  https://www.openslr.org/141/
- LibriSpeech, Panayotov, Chen, Povey and Khudanpur, 2015; dev-clean source
  corpus declared CC BY 4.0: https://www.openslr.org/12/
  Vendor archive MD5: 42e2234ba48799c1f50f24a7926300a1.
- CC BY 4.0: https://creativecommons.org/licenses/by/4.0/

Synthetic speech was generated and resampled to 16 kHz. Procedural room responses,
noise, feature masking and intermediate negative crops were applied. Real speech
was resampled and used as negative examples. No household recordings or personal
voice enrolment were used. Source licences and attribution are recorded here.
