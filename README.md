# symbolicX
This vulnerability detection tool is built on top of attackDB as part of Columbia's blockchain seminar. SymbolicX combines three major framekworks: 1) Slither; 2) Echidna; 3) Maat. We use Slither to compile contracts and extract function relations. We use Echidna for corpus generating and fuzzing; we use Maat for symbolic execution coverage tracking. 

**Architecture** 

<img width="632" alt="Screen Shot 2022-12-01 at 1 19 26 AM" src="https://user-images.githubusercontent.com/60257613/204980097-d432a37f-e996-4855-9419-4bed0c346a35.png">

**How to use the tool** 
```
git clone https://github.com/symbolicX/symbolicX && cd symbolicX 
python3 -m pip install .
```

Suppose you want to test a contract called Reentrency.sol. After installing the tool, then you can run: 

```
SymbolicX Reentrency.sol  --test-mode assertion --corpus-dir [choose your own corpus directory path] --contract Reentrency
```
Additional flags are also available to facilitate flexible symbolic execution and fuzzing: 

* ```--max-iters ```: maximum number of fuzzing iterations to perform (one iteration is one Echidna campaign + one symbolic executor run on the corpus)

* ``` --solver-timeout ```: maximum time in milliseconds to spend solving each possible new input

