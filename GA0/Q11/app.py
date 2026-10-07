from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import csv

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

with open("q-fastapi.csv", newline="", encoding="utf-8") as f:
    students = [
        {
            "studentId": int(row["studentId"]),
            "class": row["class"]
        }
        for row in csv.DictReader(f)
    ]


@app.get("/api")
async def get_students(
    class_: list[str] | None = Query(default=None, alias="class")
):
    if class_ is None:
        result = students
    else:
        result = [s for s in students if s["class"] in class_]

    return {"students": result}
