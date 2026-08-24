#include <stdio.h>
#include <stdlib.h>     /* malloc, free, rand */

#include "common.h"

#define ASIZE 65536*4
#define STEP    128
#define ITERS  4096
#define LEN    2048


typedef struct dude {
  int p1,p2,p3,p4;
} dude;


dude arr[ASIZE];
__attribute__ ((noinline))
int loop(int zero) {
  int t = 0, count=0;

  unsigned lfsr = 0xACE1u;
  do
  {
      /* taps: 18 17 16 13; feedback polynomial: x^18 + x^17 + x^16 + x^13 + 1 */
      lfsr = (lfsr >> 1) ^ (-(lfsr & 1u) & 0x39000u);
      lfsr = lfsr + arr[lfsr].p1;
  //} while(++count < ITERS);
  } while(lfsr != 0xACE1u);

  return t;
}


int main(int argc, char* argv[]) {
   argc&=10000;
   ROI_BEGIN(); 
   int t=loop(argc); 
   ROI_END();
   volatile int a = t;
}

