# Zebra — Streamlit QR clinical similarity demo

A runnable hackathon demo with three views:

- `?role=A&session=demo` — Juror A
- `?role=B&session=demo` — Juror B
- `?role=screen&session=demo` — projector / presentation screen
- `?role=admin&session=demo` — control panel + QR generator

## What the juror enters

- Name
- Age
- City
- Gender / sex (demo field)
- Pain level 0–10
- Fever
- Unintentional weight loss
- Symptoms worsening
- Symptoms waking the patient at night
- Free-text symptoms

## Encoding

Name and city are deliberately excluded from the model vector.

The first vector entries are:

1. age
2. gender — female
3. gender — male
4. gender — other / unknown

Then:

- pain
- fever
- weight loss
- worsening
- night symptoms
- text-recognized symptoms
- persistence/duration

The vector is normalized before comparison.

## Encryption demo

The app uses Paillier homomorphic encryption (`phe` package).

- Juror A's encoded vector is encrypted and retained as ciphertext.
- Juror B's vector is also encrypted for retained shared state.
- For the simple homomorphic dot-product demo, B's encoded vector is used transiently as the plaintext scalar side of Paillier multiplication against A's ciphertext.
- Only the resulting scalar similarity is decrypted.
- The projector shows only the final similarity percentage.

This is deliberately a **hackathon workflow demo**, not production healthcare cryptography.

## Run locally

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Then open:

```text
http://localhost:8501/?role=admin&session=demo
```

For testing in separate browser tabs:

```text
http://localhost:8501/?role=A&session=demo
http://localhost:8501/?role=B&session=demo
http://localhost:8501/?role=screen&session=demo
```

Submit Juror A first, then Juror B.

## Deploy to Streamlit Community Cloud

1. Create a GitHub repository.
2. Upload all files from this folder.
3. In Streamlit Community Cloud choose **Create app**.
4. Select `streamlit_app.py` as the entry point.
5. Deploy.

Once deployed, your stable URL will look similar to:

```text
https://your-zebra-demo.streamlit.app
```

Open:

```text
https://your-zebra-demo.streamlit.app/?role=admin&session=demo
```

Enter the actual app URL in the admin panel.

The app generates downloadable QR PNGs for Juror A and Juror B. You can print those QR codes beforehand and use them during the presentation.

## Recommended jury sequence

1. Project the `role=screen` page.
2. Give Juror A and Juror B the printed QR cards.
3. Ask them to enter two **synthetic** patients.
4. Ask A to submit first.
5. Ask B to submit second.
6. The projector updates automatically and displays only:

```text
87.3%
clinical similarity index
```

## Important demo limitation

Ordinary Streamlit form widgets send their values to the Streamlit server. Therefore this simplified version is **not** an end-to-end privacy implementation against the cloud host.

It is a runnable demonstration of the intended Zebra workflow:

```text
form
→ remove non-model identifiers
→ clinical encoding
→ vector
→ encryption
→ protected similarity
→ similarity only
```

For production, move de-identification/encoding/encryption client-side and replace the demo Paillier layer with audited threshold HE/MPC.

Use synthetic cases only.
