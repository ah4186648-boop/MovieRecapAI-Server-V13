MovieRecapAI — Deployment Bundle v23
====================================
ရည်ရွယ်ချက်: အင်္ဂလိပ်/တရုတ်/အခြားနိုင်ငံခြားရုပ်ရှင်၏ subtitle/transcript စာသားကို မြန်မာ narration ပြန်ရေးရန်၊ ပြီးလျှင် မြန်မာအသံ MP3 ထုတ်ရန်။ မြန်မာရုပ်ရှင်အတွက် အထူးသီးသန့်မဟုတ်ပါ။

ဒီ ZIP ကို GitHub repository root ထဲက server.py, requirements.txt, Dockerfile, render.yaml, tests ဖိုင်များကို အစားထိုးရန် ပြင်ဆင်ထားသည်။ ဒါပေမဲ့ ဒီနေရာကနေ GitHub သို့မဟုတ် Render Live service ကို ကိုယ်တိုင် ပြောင်းတင်ထားခြင်း မဟုတ်ပါ။

Render ထဲမှာ OPENAI_API_KEY ကို Environment Variable အဖြစ် သိမ်းထားပါ။ Key ကို APK ထဲ မထည့်ပါနှင့်၊ chat ထဲ မပို့ပါနှင့်။ OPENAI_API_KEY မရှိလျှင် /recap/script နှင့် /tts/long သည် 503 ပြန်ပေးမည်။

Endpoint များ:
- GET /health
- GET /capabilities
- POST /recap/transcribe-video : raw video bytes in request body; header X-Filename: movie.mp4; returns transcript (requires OPENAI_API_KEY and FFmpeg/ffprobe)
- POST /recap/script : JSON {"source_text":"...subtitle/transcript...", "target_minutes":52.5, "style":"natural"}
- POST /tts/long : JSON {"text":"မြန်မာဇာတ်ကြောင်းစာသား", "target_minutes":52.5, "style":"natural", "voice":"alloy"}

သတိ: /recap/transcribe-video သည် video အသံကို စာသားအဖြစ် ထုတ်ပေးနိုင်သည်။ သို့သော် လက်ရှိ App Inventor အက်ပ်က ဒီ endpoint ကို အလိုအလျောက်ခေါ်သုံးရန် ချိတ်ဆက်ပြီးသားဟု မယူဆပါနှင့်။ 50–55 မိနစ်သည် target သာဖြစ်ပြီး အမှန်တကယ်ကြာချိန်ကို ဇာတ်ကြောင်းစာသားအရှည်နှင့် အသံဖတ်နှုန်းက ဆုံးဖြတ်သည်။

စမ်းသပ်မှု: pytest -q ဖြင့် local smoke tests 4 ခု အောင်မြင်ခဲ့သည်။ OpenAI live API test နှင့် Android APK end-to-end test မလုပ်ရသေးပါ။

အက်ပ်ဖိုင်: app/MovieRecapAI.aia သည် MIT App Inventor project source ဖြစ်ပြီး APK မဟုတ်ပါ။ အက်ပ်ဘက်က AI script/TTS endpoints ကို တကယ်ခေါ်ကြောင်း မအတည်ပြုရသေးသောကြောင့် ဒီအထုပ်တစ်ခုတည်းကို တင်ရုံဖြင့် ဖုန်းအက်ပ်အပြည့်အဝ အလုပ်လုပ်မည်ဟု မယူဆပါနှင့်။
