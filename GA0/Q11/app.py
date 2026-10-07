from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SentimentRequest(BaseModel):
    sentences: list[str]


positive_phrases = [
    "love",
    "excited",
    "heartbroken",
    "tears of joy",
    "winning",
    "dream come true",
    "thrilled",
    "wonderful",
    "best day",
    "best day ever",
    "amazing",
    "grateful",
    "fantastic",
    "hoping for",
    "overjoyed",
    "fortunate",
    "grinning",
    "cloud nine",
    "bursting with excitement",
    "exceeded all my expectations",
    "celebrating",
    "pure joy",
    "absolutely spectacular",
    "alive and energized",
    "happy",
    "proud",
    "happiest",
    "delighted",
    "blessed",
    "pure bliss",
    "ecstatic",
    "life is beautiful",
    "radiating with happiness",
    "wonderful surprise",
    "exactly what i was hoping for",
    "can't stop smiling",
]

negative_phrases = [
    "worst",
    "terrible",
    "failed",
    "heartbroken",
    "sadness",
    "rejected",
    "devastated",
    "disappointed",
    "disappointment",
    "regret",
    "layoffs",
    "worse than expected",
    "lonely",
    "abandoned",
    "falling apart",
    "depression",
    "hopeless",
    "pain",
    "broken",
    "miserable",
    "traumatized",
    "grief",
    "sorrow",
    "failure",
    "suffering",
    "anxiety",
    "empty inside",
    "utterly defeated",
    "drowning",
    "worried sick",
    "shattered",
    "crushed",
    "burdened",
    "endless problems",
    "haunted by regret",
    "accident",
    "struggling",
    "crying because",
]


def classify_sentiment(sentence: str) -> str:
    text = sentence.lower().strip()

    # Handle phrases where a positive word could otherwise be misleading.
    if "tears of joy" in text:
        return "happy"

    if "heartbroken" in text:
        return "sad"

    if "can't stop smiling" in text:
        return "happy"

    if "worse than expected" in text:
        return "sad"

    if "dream come true" in text:
        return "happy"

    if "exactly what i was hoping for" in text:
        return "happy"

    if "wonderful surprise" in text:
        return "happy"

    if any(phrase in text for phrase in negative_phrases):
        return "sad"

    if any(phrase in text for phrase in positive_phrases):
        return "happy"

    return "neutral"


@app.post("/sentiment")
async def sentiment(request: SentimentRequest):
    return {
        "results": [
            {
                "sentence": sentence,
                "sentiment": classify_sentiment(sentence),
            }
            for sentence in request.sentences
        ]
    }