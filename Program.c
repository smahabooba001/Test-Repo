#include <stdio.h>
//adding two value
int main() {
    int value1, value2, sum;

    printf("Enter first value: ");
    scanf("%d", &value1);

    printf("Enter second value: ");
    scanf("%d", &value2);

    sum = value1 + value2;
    printf("Sum: %d\n", sum);

    return 0;
}
