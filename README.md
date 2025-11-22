# Diet Nutrition QA - Bilingual (Arabic/English) Dataset


**DietNutritionQA-ArEnDataset** is a bilingual (Arabic/English) Question–Answer dataset focused on **diet and nutrition**, designed to support and advance research in Arabic NLP.

---

## 📘 Description

A bilingual (Arabic/English) Diet & Nutrition QA dataset designed for real-world applications such as nutrition assistants, training domain-specific LLMs, and academic research. It provides a high-quality foundation for nutrition-focused language modeling across both Arabic and English.

---

## 📚 Sources

The data in this dataset is collected from:  
1. Altibbi website (Q&A by medical and nutrition professionals)  
2. Transcripts from trusted YouTube educational channels (dietitians, nutritionists, medical experts)  
3. Reputable diet & nutrition books and scientific texts

---

M. F. (2025). DietNutritionQA-ArEnDataset: A Bilingual (Arabic/English) Diet & Nutrition QA Dataset. 
Available at: https://github.com/MFHere/DietNutritionQA-ArEnDataset
License: CC0 1.0.

## 🔍 Sample QA Examples

| ID        | Question (AR)                              | Answer (AR)                                                   | Question (EN)                              | Answer (EN)                                                   |
|-----------|---------------------------------------------|----------------------------------------------------------------|---------------------------------------------|----------------------------------------------------------------|
| item-001  | ما هي فوائد البروتين؟                      | البروتين يساعد في بناء العضلات، إصلاح الأنسجة، ودعم المناعة. | What are the benefits of protein?           | Protein helps build muscle, repair tissues, and support immunity. |
| item-002  | ما هي الدهون الصحية؟                        | الدهون غير المشبعة مثل زيت الزيتون والأفوكادو تعتبر مفيدة.    | What are healthy fats?                      | Unsaturated fats like olive oil and avocado are considered healthy. |
| item-003  | ما أفضل وقت لتناول وجبة بعد التمرين؟       | يفضل تناول وجبة غنية بالبروتين والكربوهيدرات خلال 30 دقيقة. | What is the best time to eat after workout? | A protein-carb meal is recommended within 30 minutes after exercise. |
| item-004  | هل شرب الماء يساعد في فقدان الوزن؟          | نعم، يساعد في زيادة الشبع وتحسين الحرق.                      | Does drinking water help with weight loss? | Yes, it increases satiety and improves metabolic rate. |



## Citation
If you find these codes or data useful, please consider citing our paper as:
<br>
```
@dataset{DietNutriQA_AREN_2025,
  title     = {DietNutriQA-AREN: A Bilingual (Arabic/English) Diet & Nutrition QA Dataset},
  author    = {M. F.},
  year      = {2025},
  url       = {https://github.com/MFHere/DietNutritionQA-ArEnDataset},
  note      = {Creative Commons Zero v1.0 Universal (CC0)}
}
```



git clone https://github.com/MFHere/DietNutritionQA-ArEnDataset.git

## 🧪 Contribution

Contributions are welcome! If you want to:

  * Add more QA pairs
  
  * Propose corrections
  
  * Submit pull requests

Please follow the standard GitHub workflow. Make sure new entries follow the same JSONL schema.

## 🧱 Dataset Structure

```text
DietNutritionQA-ArEnDataset/
├── data/
│   ├── train.jsonl
│   ├── valid.jsonl
│   └── test.jsonl
├── docs/
│   └── data_schema.md
└── LICENSE
```

## ⚠️ Disclaimer

This dataset is for research and educational purposes only. It is not a substitute for professional medical or dietary advice. Always consult a qualified health professional for personalized nutrition guidance.


Thank you for using DietNutritionQA-ArEnDataset — your research and feedback are highly appreciated!

<p align="center">
  <img src="https://img.shields.io/badge/License-CC0_1.0-lightgrey.svg" />
  <img src="https://img.shields.io/badge/Language-AR%2FEN-green" />
  <img src="https://img.shields.io/badge/Dataset_Type-QA-blue" />
  <img src="https://img.shields.io/badge/Domain-Diet%20%26%20Nutrition-orange" />
</p>


 
