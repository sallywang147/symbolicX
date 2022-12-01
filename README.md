# symbolicX
This vulnerability detection tool is built on top of attackDB as part of Columbia's blockchain seminar. SymbolicX combines three major framekworks: 1) Slither; 2) Echidna; 3) Maat. We use Slither to compile contracts and extract function relations. We use Echidna for corpus generating and fuzzing; we use Maat for symbolic execution coverage tracking. 

**Architecture** 

<img width="632" alt="Screen Shot 2022-12-01 at 1 19 26 AM" src="https://user-images.githubusercontent.com/60257613/204980097-d432a37f-e996-4855-9419-4bed0c346a35.png">

**How to use the tool** 
