# Fantasy Lottery Telegram Bot

## Overall Idea
The plan is to make a telegram bot that runs a fantasy lottery game where players draft numbers (from 1 to 70) and get points when their numbers appear in a lottery draw.
The bot should be able to run a snake draft where each player drafts 6 numbers each.
The bot should check https://www.lottodatabase.com every day (at a scheduled time) and update the points for each player and send a summary of lottery draws and player point changes for that day.
The bot should also allow for trading of numbers between players and adding/dropping numbers (ie a player can drop a number to pick up a number not used by any player). These mechanics should ensure that a player ALWAYS has 6 numbers.


## Scoring rules
All points 5x for Mega Millions and Power Ball
The extra number/power ball is worth .25 and doesn't count for multiple number bonuses

1 number: 1 point
2 numbers in one lottery: 5 points 
3 numbers: 40 points
4 numbers: 800 points
5 numbers: 50,000 points
Complete match (ie all numbers in lottery number): 20,000,000 points


## Lotteries
Below are the lotteries we are going to use with associated information:
```
Main Balls	Main Balls To	Power Ball To	Weekly Draws
Mega Millions	USA	5	70	24	2
Powerball	USA	5	69	26	3
Millionaire for Life	USA	5	58	5	7
The Pick	Arizona	6	44	x	3
SuperLotto Plus	California	5	47	27	2
Colorado Lotto	Colorado	6	40	x	3
Florida Lotto	Florida	6	53	x	2
Hoosier Lotto	Indiana	6	46	x	2
Michigan Lotto 47	Michigan	6	47	x	2
Pick-6	New Jersey	6	46	x	3
New York Lotto	New York	6	59	59	2
Classic Lotto	Ohio	6	49	x	3
Match 6	Pennsylvania	6	49	x	7
Lotto Texas	Texas	6	54	x	3
Bank A Million	Virginia	6	40	40	2
Washington Lotto	Washington	6	49	x	3
Daily Grand	Canada	5	49	7	2
Lotto 6/49	Canada	6	49	49	2
Atlantic 49	Atlantic Canada	6	49	49	2
BC/49	British Columbia	6	49	49	2
Lottario	Ontario	6	45	45	1
Ontario 49	Ontario	6	49	49	2
Quebec 49	Quebec	6	49	49	2
Western 649	Western Canada	6	49	49	1
```
