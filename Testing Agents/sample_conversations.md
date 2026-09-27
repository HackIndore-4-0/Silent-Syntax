# Sample Conversations

These are illustrative transcripts for exercising the agent and an external
Agent Evaluator. All flight numbers, prices, and booking IDs are demo data
generated at runtime — actual output will vary slightly by LLM response.

## 1. Context retention across turns

```
User: I want to fly from Indore to Delhi.
Agent: Sure! What date would you like to travel?
User: October 15, 2026.
Agent: Great, and how many passengers will be travelling?
User: Two.
Agent: [calls search_flights(source="Indore", destination="Delhi", date="2026-10-15", passengers=2)]
       Here are the available flights from Indore to Delhi on 2026-10-15 for 2 passengers:
       1. IndiGo 6E-204 — 08:00 → 09:40 (1h 40m) — ₹4500 — 24 seats left
       2. Air India AI-408 — 14:30 → 16:10 (1h 40m) — ₹5200 — 12 seats left
       Would you like to book one of these?
```

## 2. Booking with explicit confirmation

```
User: Book flight F001 for me. My name is Rahul Verma, age 29, email rahul@example.com.
Agent: To confirm: book IndiGo 6E-204 (Indore → Delhi, 2026-10-15, 08:00-09:40) for
       Rahul Verma (age 29) at ₹4500. Shall I proceed? (yes/no)
User: Yes, please confirm.
Agent: [calls book_flight(flight_id="F001", passengers=[...], user_confirmation=true)]
       Booked! Your demo booking ID is BK-A1B2C3D4. This is a demo booking — no real
       ticket has been issued.
```

## 3. Refusing to book without confirmation

```
User: Book flight F002 for me, John Doe, 40, john@example.com.
Agent: Before I book this, please confirm: IndiGo... Air India AI-408 (Indore → Delhi,
       2026-10-15, 14:30-16:10) for John Doe at ₹5200 — shall I proceed?
User: Actually, hold on.
Agent: No problem, I won't book anything until you confirm.
```

## 4. Booking retrieval and cancellation

```
User: What's the status of booking BK-A1B2C3D4?
Agent: [calls get_booking(booking_id="BK-A1B2C3D4")]
       Booking BK-A1B2C3D4 is confirmed: IndiGo 6E-204, Indore → Delhi, 2026-10-15,
       1 passenger (Rahul Verma), total ₹4500.
User: Please cancel it.
Agent: Just to confirm — you'd like to cancel booking BK-A1B2C3D4 (IndiGo 6E-204,
       ₹4500)? This can't be undone in this demo. (yes/no)
User: Yes.
Agent: [calls cancel_booking(booking_id="BK-A1B2C3D4", user_confirmation=true)]
       Done — booking BK-A1B2C3D4 has been cancelled.
```

## 5. Handling unavailable flights / alternatives

```
User: I need a flight from Bengaluru to Chennai on 2026-10-16.
Agent: [calls search_flights(source="Bengaluru", destination="Chennai", date="2026-10-16", passengers=1)]
       Flight F008 (Vistara UK-812) matches that route and date, but it currently has
       0 seats available. There are no other direct flights on this route for that
       date in the demo data. Would you like me to check a nearby date or a different
       route (e.g., via Hyderabad)?
```

## 6. Recommendations with an explanation

```
User: Recommend a flight from Mumbai to Delhi under 6500 rupees, preferably in the morning.
Agent: [calls recommend_flights(source="Mumbai", destination="Delhi", date="2026-10-16", budget=6500, preferred_time="morning")]
       I'd recommend Akasa Air QP-1755 (05:30 → 07:40, ₹6300) — it fits your budget of
       ₹6500 and departs in the morning as you asked.
```

## 7. Missing / invalid information

```
User: Book me a flight.
Agent: I'd be happy to help! Could you tell me the flight ID you'd like to book, and
       the passenger's full name, age, and email?

User: Search flights from Pune to Delhi on 15th October.
Agent: Could you give me the date in YYYY-MM-DD format, e.g. 2026-10-15? Also note
       Pune isn't in this demo's flight network — available cities are Indore, Delhi,
       Mumbai, Bengaluru, Hyderabad, Chennai, and Kolkata.
```

## 8. Ambiguous requests

```
User: I want to go to Delhi.
Agent: Sure — where will you be departing from, and on what date?
```

## 9. Nonexistent flight / booking IDs

```
User: Get details for flight F999.
Agent: [calls get_flight_details(flight_id="F999")]
       I couldn't find a flight with ID F999 in our demo database. Would you like to
       search by route and date instead?

User: Show me booking BK-ZZZZZZZZ.
Agent: [calls get_booking(booking_id="BK-ZZZZZZZZ")]
       I couldn't find a booking with ID BK-ZZZZZZZZ. Please double-check the booking ID.
```
