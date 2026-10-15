import os
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)

# ရုပ်ရှင်စာရင်းများနှင့် ဇာတ်လမ်းအကျဉ်းများ (Database ပုံစံထည့်သွင်းထားခြင်း)
MOVIES_DATABASE = {
    "Avatar: The Way of Water": (
        "ဤရုပ်ရှင်သည် Pandora ဂြိုဟ်ပေါ်ရှိ Na’vi လူမျိုးများ၏ ရေအောက်ကမ္ဘာကို"
        " နောက်ခံထား၍ Jake Sully နှင့် မိသားစုတို့၏ အသက်ရှင်ရပ်တည်ရေး"
        " တိုက်ပွဲများကို ရိုက်ကူးထားသည်။"
    ),
    "Inception": (
        "လူတို့၏ အိပ်မက်ထဲသို့ ဝင်ရောက်ကာ အသိဉာဏ်များကို ခိုးယူခြင်းနှင့်"
        " စိုက်ပျိုးခြင်း (Inception) ပြုလုပ်သော သူခိုးတစ်စုအကြောင်း"
        " စိတ်ကူးယဉ်ဆန်ဆန် ရိုက်ကူးထားသည်။"
    ),
    "Interstellar": (
        "ကမ္ဘာမြေကြီး ပျက်စီးလာသောအခါ လူသားမျိုးနွယ်အတွက် အသိုက်အမြုံသစ်"
        " ရှာဖွေရန် အာကာသထဲသို့ ခရီးနှင်ကြသည့် အာကာသယာဉ်မှူးများ၏"
        " ခရီးစဉ်ကို တင်ဆက်ထားသည်။"
    ),
    "The Avengers": (
        "ကမ္ဘာကြီးကို ကယ်တင်ရန် စူပါဟီးရိုးများဖြစ်သော Iron Man, Captain America,"
        " Thor နှင့် အခြားသူများ ပူးပေါင်းတိုက်ခိုက်ကြပုံကို ရိုက်ကူးထားသည်။"
    ),
    "Titanic": (
        "ကမ္ဘာကျော် တိုင်တានစ်သင်္ဘောကြီး မြုပ်မည့်ကံကြမ္မာနှင့်အတူ"
        " ဖြစ်ပေါ်လာသည့် ဂျက်နှင့် ရို့စ်တို့၏ ကြေကွဲဖွယ် အချစ်ဇာတ်လမ်းဖြစ်သည်။"
    ),
    "Spider-Man: No Way Home": (
        "ပီတာပါကာသည် မိမိ၏ လျှို့ဝှက်ချက် ထჟောက်ကြားသွားသောအခါ"
        " ဒေါက်တာစထရိနှင့်အတူ မာတီဗတ်စ်ကို ဖွင့်မိပြီး စကြဝဠာအသီးသီးမှ"
        " ရန်သူများနှင့် ရင်ဆိုင်ရပုံကို ရိုက်ကူးထားသည်။"
    ),
    "Black Panther: Wakanda Forever": (
        "ဝါကန်ဒါနိုင်ငံသားများသည် ဘုရင် T’Challa ကွယ်လွန်သွားပြီးနောက်"
        " နိုင်ငံကို ကာကွယ်ရန် ရေအောက်နိုင်ငံတော်မှ တိုက်ခိုက်မှုများကို"
        " ရင်ဆိုင်ကြရသည်။"
    ),
    "Doctor Strange in the Multiverse of Madness": (
        "ဒေါက်တာစထရိသည် မာတီဗတ်စ်၏ အန္တရာယ်အမျိုးမျိုးကို ကာကွယ်ရင်း"
        " အစွမ်းထက်သော မိန်းကလေးတစ်ဦးကို ကယ်တင်ရန် ကြိုးပမ်းပုံကို"
        " တင်ဆက်ထားသည်။"
    ),
    "Thor: Love and Thunder": (
        "သောရ်သည် အတွေးအခေါ်အသစ်များဖြင့် ငြိမ်းချမ်းရေးရှာဖွေနေစဉ်"
        " ဘုရားသတ္တဝါများကို သတ်ဖြတ်နေသည့် Gorr the God Butcher ကို"
        " ရင်ဆိုင်တိုက်ခိုက်ရသည်။"
    ),
    "Top Gun: Maverick": (
        "မက်ဗရစ်သည် ထူးချွန်သော လေယာဉ်မှူးအသစ်များကို အန္တရာယ်ကြီးမားသော"
        " မစ်ရှင်တစ်ခုအတွက် လေ့ကျင့်သင်ကြားပေးရင်း သူ၏ အတိတ်ကို"
        " ရင်ဆိုင်ရပုံဖြစ်သည်။"
    ),
}


@app.route('/movies', methods=['GET'])
def get_movies():
  return jsonify({'movies': list(MOVIES_DATABASE.keys())})


@app.route('/recap', methods=['GET', 'POST'])
def get_recap():
  movie_name = request.args.get('movie', '')
  if not movie_name and request.is_json:
    data = request.get_json()
    movie_name = data.get('movie', 'Inception')

  if not movie_name:
    movie_name = 'Inception'

  # ဒေတာဘေ့စ်ထဲတွင် ရှိပါက ထုတ်ပေးမည်
  if movie_name in MOVIES_DATABASE:
    summary = MOVIES_DATABASE[movie_name]
  else:
    # အကယ်၍ OpenAI API Key ရှိပါက AI ဖြင့် အလိုအလျောက်ထုတ်ပေးမည်
    api_key = os.environ.get('OPENAI_API_KEY')
    if api_key:
      try:
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }
        payload = {
            'model': 'gpt-3.5-turbo',
            'messages': [{
                'role': 'user',
                'content': (
                    'Give a short movie recap in Burmese for: ' f'{movie_name}'
                ),
            }],
            'max_tokens': 300,
        }
        response = requests.post(
            'https://api.openai.com/v1/chat/completions',
            json=payload,
            headers=headers,
            timeout=10,
        )
        if response.status_code == 200:
          res_json = response.json()
          summary = res_json['choices'][0]['message']['content'].strip()
        else:
          summary = (
              f"'{movie_name}' ရုပ်ရှင်အတွက် ဇာတ်လမ်းအကျဉ်းကို အသေးစိတ်"
              ' ဖော်ပြပေးထားပါသည်။'
          )
      except Exception:
        summary = (
            f"'{movie_name}' ရုပ်ရှင်သည် စိတ်ဝင်စားစရာကောင်းသော"
            ' ဇာတ်အိမ်ဖြင့် ရိုက်ကူးထားသည်။'
        )
    else:
      summary = (
          f"'{movie_name}' ရုပ်ရှင်သည် စိတ်ဝင်စားစရာကောင်းသော"
          ' ဇာတ်အိမ်ဖြင့် ရိုက်ကူးထားသည့် ရုပ်ရှင်ကားကောင်းတစ်ကား ဖြစ်ပါသည်။'
      )

  return jsonify({'movie': movie_name, 'recap': summary})


@app.route('/', methods=['GET'])
def home():
  return jsonify({
      'status': 'success',
      'message': 'Movie Recap AI Advanced Server is running live!',
      'total_movies': len(MOVIES_DATABASE),
      'endpoints': ['/movies', '/recap?movie=MovieName'],
  })


if __name__ == '__main__':
  app.run(host='0.0.0.0', port=5000)

